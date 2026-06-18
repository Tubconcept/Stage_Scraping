"""
Scraper Sonepar — mode mise à jour par liste de références (P8).

Format des références Sonepar : 11 chiffres, ex. 05594001205.

Pour chaque référence fournie :
  1. Construit l'URL directe : BASE_URL + "products/" + ref
  2. Navigue vers la fiche produit et extrait toutes les données.
  3. Upsert en MariaDB (INSERT ou UPDATE selon l'existence de la référence).

Factory attendue par la GUI :
    create_scraper(refs: list[str]) → SoneparByRefsScraper
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os

from dotenv import load_dotenv

from css_selectors.sonepar import Selectors
from core.config import PROFILES_DIR
from core.base_scraper import BaseScraper
from core.logger import log_exception
from db.mariadb_db import init_site_db, upsert_product, resolve_decli_index

try:
    from .scraper_sonepar_products import _SoneparCSS
except ImportError:
    from scraper_sonepar_products import _SoneparCSS  # type: ignore[no-redef]

load_dotenv()


class SoneparByRefsScraper(_SoneparCSS):
    """Scrape une liste ciblée de références Sonepar par URL directe.

    Chaque référence est un code à 11 chiffres (ex. 05594001205).
    L'URL produit est construite comme : BASE_URL + "products/" + ref

    Hérite de _SoneparCSS pour réutiliser :
      - _connexion / _is_logged_in / _ensure_logged
      - _safe_goto_product / _get_product_data
      - _ariane / to_csv_row
    """

    def __init__(self, refs: list[str]) -> None:
        BaseScraper.__init__(self, "sonepar_by_refs")
        self._refs = refs
        self._username = os.getenv("User_P8", "")
        self._password = os.getenv("Password_P8", "")
        self._db_conn = None

    # ─── Persistance ─────────────────────────────────────────────────────────

    def _apply_combination_index(self, products: list[dict]) -> None:
        if not products:
            return
        parent_ref = products[0].get("parent", "") or ""
        try:
            grp_idx = resolve_decli_index("sonepar", parent_ref)
            for p in products:
                if p.get("IsCombination"):
                    p["IndexCombination"] = grp_idx
        except Exception:
            pass

    def _upsert_products(
        self,
        products: list[dict],
        cat1: str,
        cat2: str,
        cat3: str,
        product_url: str,
    ) -> int:
        count = 0
        for product in products:
            row = self.to_csv_row(product, cat1, cat2, cat3, product_url)
            if self._db_conn is not None:
                try:
                    upsert_product(self._db_conn, "sonepar", row)
                except Exception as exc:
                    self.log.debug("MariaDB ignoré : %s", exc)
            count += 1
        return count

    # ─── Session ─────────────────────────────────────────────────────────────

    async def _ensure_session(self, page, storage_path: Path) -> None:
        await page.goto(Selectors.BASE_URL)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.locator(Selectors.cookie_accept_button).click(timeout=3000)
        except Exception:
            pass
        if not await self._is_logged_in(page):
            self.log.info("Session expirée — reconnexion")
            await self._connexion(page)
            await self.save_storage_state(storage_path)
        else:
            self.log.info("Session active")

    # ─── Traitement par référence ─────────────────────────────────────────────

    async def _process_ref(self, page, ref: str) -> int:
        """Navigue vers la fiche produit (11 chiffres) et upsert. Retourne le nb de lignes."""
        self.log.info("[%s] ── Début ──", ref)

        # Référence Sonepar : 11 chiffres → URL directe /products/{ref}
        product_url = Selectors.BASE_URL.rstrip("/") + "/products/" + ref
        ok = await self._safe_goto_product(page, product_url)
        if not ok:
            self.log.warning("[%s] Page produit introuvable : %s", ref, product_url)
            return 0

        await self._ensure_logged(page)
        has_combo, products = await self._get_product_data(page, product_url)
        if has_combo and products:
            self._apply_combination_index(products)

        if not products:
            self.log.warning("[%s] Aucune donnée extraite", ref)
            return 0

        cat1, cat2, cat3 = await self._ariane(page)
        count = self._upsert_products(products, cat1, cat2, cat3, product_url)
        self.log.info("[%s] ✓ %d variante(s) enregistrée(s)", ref, count)
        return count

    # ─── Boucle principale ────────────────────────────────────────────────────

    async def run(self) -> None:
        try:
            self._db_conn = init_site_db("sonepar")
        except Exception as exc:
            self.log.warning("Base MariaDB non initialisée : %s", exc)
            self._db_conn = None

        storage_path = PROFILES_DIR / "sonepar" / "session.json"
        storage_state = str(storage_path) if storage_path.exists() else None

        await self.start_browser(headless=False, storage_state=storage_state)
        page = await self.new_page()
        rows_written = 0

        try:
            await self._ensure_session(page, storage_path)
            self.log.info(
                "Mode références Sonepar : %d référence(s)", len(self._refs)
            )
            for ref in self._refs:
                if self.should_stop():
                    self.log.info("Arrêt demandé")
                    break
                try:
                    rows_written += await self._process_ref(page, ref)
                except Exception as exc:
                    log_exception(self.log, exc, f"Référence {ref}")
        except Exception as exc:
            log_exception(self.log, exc, "run() sonepar_by_refs")
        finally:
            if self._db_conn is not None:
                try:
                    self._db_conn.close()
                except Exception:
                    pass
                self._db_conn = None
            await self.close()

        self.log.info(
            "Terminé — %d variante(s) enregistrée(s) en MariaDB", rows_written
        )


def create_scraper(refs: list[str]) -> SoneparByRefsScraper:
    """Factory attendue par la GUI."""
    return SoneparByRefsScraper(refs=refs)
