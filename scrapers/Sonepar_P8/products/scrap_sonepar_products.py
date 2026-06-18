"""
Orchestrateur du scraper produits Sonepar (site P8).

Rôle :
    Point d'entrée officiel pour l'extraction du catalogue produits Sonepar.
    Ce fichier coordonne le navigateur Playwright, la session utilisateur,
    la boucle de scraping et la persistance en base MariaDB.

Type : produits.

Architecture :
    - scrap_sonepar_products.py (ce fichier) = orchestrateur : run(), persistance,
      gestion session, pagination métier, reprise sur crash.
    - scraper_sonepar_products.py = moteur CSS : sélecteurs, navigation catégories,
      extraction HTML des fiches produit et variantes.

Consommateurs : GUI (create_scraper), CLI (--limit).
"""

# ─── Bootstrap du chemin projet ───────────────────────────────────────────────

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ─── Imports standards et métier ──────────────────────────────────────────────

import argparse
import asyncio
import os
import re
from collections import deque

from dotenv import load_dotenv

from css_selectors.sonepar import Selectors
from core.config import PROFILES_DIR
from core.base_scraper import BaseScraper
from core.logger import log_exception
from db.mariadb_db import (
    init_site_db,
    insert_product,
    get_scraped_product_urls,
    resolve_decli_index,
)

try:
    from .scraper_sonepar_products import _SoneparCSS
except ImportError:
    from scraper_sonepar_products import _SoneparCSS  # type: ignore[no-redef]

load_dotenv()


# ─── Classe orchestratrice ────────────────────────────────────────────────────

class SoneparProductScraper(_SoneparCSS):
    """Orchestration produits Sonepar + persistance MariaDB.

    Hérite de _SoneparCSS pour réutiliser :
      - _connexion / _is_logged_in / _ensure_logged
      - _get_categories / _collect_product_links
      - _safe_goto_product / _get_product_data
      - _ariane / to_csv_row
    """

    def __init__(self, category_name: str = "") -> None:
        BaseScraper.__init__(self, "sonepar_products")
        self._username = os.getenv("User_P8", "")
        self._password = os.getenv("Password_P8", "")
        if not self._username or not self._password:
            self.log.warning("User_P8 ou Password_P8 non défini dans .env")
        # Si une catégorie est sélectionnée, on filtre sur son nom dans le mega-menu
        self._category_filter: list[str] = [category_name] if category_name else []
        self._db_conn = None

    # ─── Persistance MariaDB ──────────────────────────────────────────────────

    def _persist_product(
        self,
        product: dict,
        cat1: str,
        cat2: str,
        cat3: str,
        source_url: str,
    ) -> None:
        row = self.to_csv_row(product, cat1, cat2, cat3, source_url)
        if self._db_conn is not None:
            try:
                insert_product(self._db_conn, "sonepar", row)
            except Exception as exc:
                self.log.debug("MariaDB produit ignoré : %s", exc)

    def _apply_combination_index(self, products: list[dict]) -> None:
        """Résout et assigne IndexCombination via la séquence MariaDB."""
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

    async def _scrape_and_persist(
        self, page, link: str, t1: str = "", t2: str = ""
    ) -> tuple[int, list[str]]:
        """Navigue, extrait et persiste un produit.

        t1 : catégorie T1 connue depuis le mega-menu (garantie correcte).
        t2 : catégorie T2 connue depuis le mega-menu (garantie correcte).

        Returns:
            (rows_written, sibling_links) — sibling_links : liens des autres
            variantes du même groupe à traiter immédiatement pour garder le
            même product_combination_index consécutif en base.
        """
        full_url = Selectors.BASE_URL.rstrip("/") + link
        ok = await self._safe_goto_product(page, full_url)
        if not ok:
            log_exception(
                self.log, Exception("Produit ignoré"), f"_safe_goto échoué : {link}"
            )
            return 0, []

        await self._ensure_logged(page)
        has_combo, products = await self._get_product_data(page, full_url)
        if has_combo and products:
            self._apply_combination_index(products)

        # Lire les 3 niveaux de catégorie directement depuis le fil d'Ariane du produit
        bc1, bc2, bc3 = await self._get_categories_from_breadcrumb(page)
        # Fallback mega-menu si le breadcrumb est vide (page non chargée)
        cat1 = bc1 or t1
        cat2 = bc2 or t2
        cat3 = bc3
        for product in products:
            self._persist_product(product, cat1, cat2, cat3, full_url)

        # Calculer les liens des autres variantes du même groupe
        # On utilise les chemins complets (avec slug) pour éviter les 404
        sibling_links: list[str] = []
        if has_combo and products:
            ref_lier = products[0].get("ref_lier", "")
            variant_paths_str = products[0].get("variant_paths", "")
            if ref_lier and variant_paths_str:
                slug = link.split("/products/")[-1]
                m = re.match(r"^(\d{11})", slug)
                current_ref = m.group(1) if m else ""
                refs = ref_lier.split("|")
                paths = variant_paths_str.split("|")
                for ref, path in zip(refs, paths):
                    if ref and ref != current_ref and path:
                        sibling_links.append(path)

        return len(products), sibling_links

    # ─── Session ─────────────────────────────────────────────────────────────

    async def _ensure_session(self, page, storage_path: Path) -> None:
        """Navigue vers l'accueil, accepte les cookies et reconnecte si besoin."""
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

    def _load_seen_urls(self) -> set[str]:
        """Charge les URLs déjà en base pour reprendre un scraping interrompu."""
        seen: set[str] = (
            get_scraped_product_urls(self._db_conn, "sonepar")
            if self._db_conn is not None
            else set()
        )
        if seen:
            self.log.info(
                "Reprise — %d URL(s) déjà scrappée(s) ignorées", len(seen)
            )
        return seen

    # ─── Mode catalogue ───────────────────────────────────────────────────────

    async def _run_catalogue_mode(
        self,
        nav_page,
        product_page,
        seen: set[str],
        limit_products: int | None,
    ) -> int:
        """Parcourt toutes les catégories et scrape chaque produit. Retourne le total écrit."""
        cat_infos = await self._get_categories(nav_page, self._category_filter or None)
        self.log.info("Mode catalogue : %d catégorie(s)", len(cat_infos))
        rows_written = 0

        for t1, t2, cat_url in cat_infos:
            if self.should_stop():
                break
            try:
                product_links = await self._collect_product_links(nav_page, cat_url)
                self.log.info(
                    "Catégorie %s : %d produit(s)", cat_url, len(product_links)
                )
                # Deque : permet d'injecter les variantes au front pour les garder
                # consécutives dans la base (même product_combination_index adjacent)
                queue: deque[str] = deque(product_links)
                while queue:
                    if self.should_stop():
                        break
                    if limit_products is not None and rows_written >= limit_products:
                        break
                    link = queue.popleft()
                    full_url = Selectors.BASE_URL.rstrip("/") + link
                    if full_url in seen:
                        continue
                    seen.add(full_url)
                    try:
                        n, sibling_links = await self._scrape_and_persist(
                            product_page, link, t1=t1, t2=t2
                        )
                        rows_written += n
                        # Injecter les frères non vus en tête de file
                        for slink in reversed(sibling_links):
                            sfull = Selectors.BASE_URL.rstrip("/") + slink
                            if sfull not in seen:
                                queue.appendleft(slink)
                    except Exception as exc:
                        log_exception(self.log, exc, f"Produit {link}")
            except Exception as exc:
                log_exception(self.log, exc, f"Catégorie {cat_url}")

        return rows_written

    # ─── Point d'entrée principal ─────────────────────────────────────────────

    async def run(self, limit_products: int | None = None) -> None:
        """Lance le scraping : session → catalogue → MariaDB."""
        try:
            self._db_conn = init_site_db("sonepar")
        except Exception as exc:
            self.log.warning("Base MariaDB non initialisée : %s", exc)
            self._db_conn = None

        storage_path = PROFILES_DIR / "sonepar" / "session.json"
        storage_state = str(storage_path) if storage_path.exists() else None

        await self.start_browser(headless=False, storage_state=storage_state)
        nav_page = await self.new_page()
        product_page = await self.new_page()
        rows_written = 0

        try:
            await self._ensure_session(nav_page, storage_path)
            seen = self._load_seen_urls()
            rows_written = await self._run_catalogue_mode(
                nav_page, product_page, seen, limit_products
            )
        except Exception as exc:
            log_exception(self.log, exc, "run() sonepar_products")
        finally:
            if self._db_conn is not None:
                try:
                    self._db_conn.close()
                except Exception:
                    pass
                self._db_conn = None
            await self.close()

        self.log.info(
            "Terminé — %d produit(s) enregistré(s) en MariaDB", rows_written
        )


# ─── Factory et CLI ───────────────────────────────────────────────────────────

def create_scraper(category_name: str = "") -> SoneparProductScraper:
    """Factory attendue par la GUI."""
    return SoneparProductScraper(category_name=category_name)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper produits Sonepar → MariaDB")
    parser.add_argument(
        "--limit", type=int, default=None, help="Limite de produits à scraper"
    )
    args = parser.parse_args()

    limit = args.limit
    if limit is None and os.getenv("SONEPAR_PRODUCT_LIMIT", "").isdigit():
        limit = int(os.getenv("SONEPAR_PRODUCT_LIMIT", ""))

    scraper = create_scraper()
    await scraper.run(limit_products=limit)


if __name__ == "__main__":
    asyncio.run(main())
