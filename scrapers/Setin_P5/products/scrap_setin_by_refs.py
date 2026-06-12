"""Scraper Setin — mode mise à jour par liste de références (P5).

Pour chaque référence fournie :
  1. Retourne à l'accueil Setin.
  2. Remplit la barre de recherche avec la référence et soumet.
  3. Si Setin redirige directement vers la fiche produit → scrape.
  4. Sinon cherche le premier lien produit dans les résultats et y navigue.
  5. Extrait toutes les données via _get_product_data (même logique catalogue).
  6. Insère dans MariaDB (mêmes tables, mêmes règles que le mode catalogue).

Factory attendue par la GUI :
    create_scraper(refs: list[str]) → SetinByRefsScraper
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os

from dotenv import load_dotenv

from css_selectors.setin import Selectors
from core.config import PROFILES_DIR, TIMEOUT_MEDIUM
from core.base_scraper import BaseScraper
from core.logger import log_exception
from db.mariadb_db import init_site_db, upsert_product

try:
    from .scraper_setin_products import SetinProductScraper as _SetinCSS
except ImportError:
    from scrapers.Setin_P5.products.scraper_setin_products import SetinProductScraper as _SetinCSS  # type: ignore[no-redef]

load_dotenv(_PROJECT_ROOT / ".env")

# Sélecteurs de fallback pour trouver un lien produit dans une page de résultats
_PRODUCT_LINK_SELECTORS = [
    "div.product_box div.bp_image a",   # catégories (sélecteur principal)
    "div.product_box a",                # produit box — lien plus large
    ".product_box a",                   # variante sans div
    "a[href*='article']",               # URL contenant "article"
    "a[href*='produit']",               # URL contenant "produit"
    "a[href*='fiche']",                 # URL contenant "fiche"
]


class SetinByRefsScraper(_SetinCSS):
    """Scrape une liste ciblée de références via la barre de recherche Setin.

    Hérite de SetinProductScraper pour réutiliser :
      - _is_logged_in / _connexion / save_storage_state
      - _get_product_data  (extraction complète d'une fiche + toutes variantes)
      - _ariane            (breadcrumb cat1/cat2/cat3)
      - to_csv_row         (mapping interne → CSV_HEADERS)
    """

    def __init__(self, refs: list[str]) -> None:
        BaseScraper.__init__(self, "setin_by_refs")
        self._refs = refs
        self._username = os.getenv("User_P5", "")
        self._password = os.getenv("Password_P5", "")
        self._csv_path: Path | None = None   # compat GUI
        self._db_conn  = None
        self._stop     = False

    def request_stop(self) -> None:
        self._stop = True

    def should_stop(self) -> bool:
        return self._stop or super().should_stop()

    # ─── Helpers recherche ───────────────────────────────────────────────────

    async def _find_search_box(self, page):
        """Retourne le premier input de recherche visible, ou None."""
        for sel in Selectors.search_input_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible():
                    self.log.info("Barre de recherche trouvée : %s", sel)
                    return loc
            except Exception:
                continue
        return None

    async def _find_product_href(self, page) -> str | None:
        """Cherche le premier lien produit dans la page avec plusieurs sélecteurs."""
        for selector in _PRODUCT_LINK_SELECTORS:
            try:
                loc = page.locator(selector)
                if await loc.count() > 0:
                    href = await loc.first.get_attribute("href")
                    if href:
                        self.log.debug("Produit trouvé via sélecteur : %s", selector)
                        return href
            except Exception:
                continue
        return None

    # ─── Boucle principale ────────────────────────────────────────────────────

    async def run(self) -> None:
        try:
            self._db_conn = init_site_db("setin")
        except Exception as exc:
            self.log.warning("Base MariaDB non initialisée : %s", exc)
            self._db_conn = None

        storage_path  = PROFILES_DIR / "setin" / "session.json"
        storage_state = str(storage_path) if storage_path.exists() else None

        await self.start_browser(headless=False, storage_state=storage_state)
        page = await self.new_page()
        rows_written = 0

        try:
            # ── Connexion ────────────────────────────────────────────────────
            await page.goto(Selectors.BASE_URL)
            await page.wait_for_load_state("domcontentloaded")
            try:
                await page.locator(Selectors.page_loader).wait_for(
                    state="hidden", timeout=10000
                )
            except Exception:
                pass
            try:
                await page.locator(Selectors.home_return_button).first.click(
                    timeout=TIMEOUT_MEDIUM
                )
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass

            if not await self._is_logged_in(page):
                self.log.info("Session expirée — reconnexion")
                await self._connexion(page)
                await self.save_storage_state(storage_path)
            else:
                self.log.info("Session active")

            self.log.info("Mode références Setin : %d référence(s)", len(self._refs))
            current_index = 1

            for ref in self._refs:
                if self.should_stop():
                    self.log.info("Arrêt demandé")
                    break

                self.log.info("[%s] ── Début ──", ref)

                try:
                    # ── 1. Retour à l'accueil ─────────────────────────────────
                    await page.goto(Selectors.BASE_URL)
                    await page.wait_for_load_state("domcontentloaded")
                    try:
                        await page.locator(Selectors.page_loader).wait_for(
                            state="hidden", timeout=5000
                        )
                    except Exception:
                        pass

                    # ── 2. Trouver la barre de recherche ─────────────────────
                    search_box = await self._find_search_box(page)
                    if search_box is None:
                        self.log.error(
                            "[%s] Barre de recherche introuvable sur %s — "
                            "vérifiez les sélecteurs dans css_selectors/setin.py",
                            ref, page.url,
                        )
                        continue

                    # ── 3. Taper la référence et soumettre ────────────────────
                    await search_box.click()
                    await page.wait_for_timeout(200)
                    await search_box.fill("")              # vider d'abord
                    await search_box.fill(str(ref))        # fill = fiable + déclenche events
                    await page.wait_for_timeout(300)
                    await search_box.press("Enter")        # soumettre le formulaire

                    # Attendre le chargement (navigation ou AJAX)
                    await page.wait_for_load_state("domcontentloaded")
                    await page.wait_for_timeout(1500)      # marge pour les contenus AJAX
                    try:
                        await page.locator(Selectors.page_loader).wait_for(
                            state="hidden", timeout=5000
                        )
                    except Exception:
                        pass

                    current_url = page.url
                    self.log.info("[%s] URL après recherche : %s", ref, current_url)

                    # ── 4. Déterminer l'URL de la fiche produit ───────────────
                    product_url: str | None = None

                    # Cas A : Setin a redirigé directement vers la fiche
                    if await page.locator(Selectors.table_wait).count() > 0:
                        product_url = current_url
                        self.log.info("[%s] Redirection directe → fiche produit", ref)

                    # Cas B : page de résultats (URL ou DOM)
                    else:
                        href = await self._find_product_href(page)
                        if not href:
                            self.log.warning("[%s] Aucun produit trouvé dans les résultats", ref)
                            continue
                        product_url = (
                            href if href.startswith("http")
                            else f"{Selectors.BASE_URL.rstrip('/')}/{href.lstrip('/')}"
                        )
                        self.log.info("[%s] 1er produit trouvé : %s", ref, product_url)

                    # ── 5. Extraire la fiche produit ──────────────────────────
                    current_index, products = await self._get_product_data(
                        page, product_url, current_index
                    )

                    if not products:
                        self.log.warning("[%s] Aucune donnée extraite de : %s", ref, product_url)
                        continue

                    # ── 6. Catégories + sauvegarde MariaDB ────────────────────
                    cat1, cat2, cat3 = await self._ariane(page)

                    for produit in products:
                        row = self.to_csv_row(produit, cat1, cat2, cat3, product_url)
                        if self._db_conn is not None:
                            try:
                                upsert_product(self._db_conn, "setin", row)
                            except Exception as exc:
                                self.log.debug("MariaDB ignoré : %s", exc)
                        rows_written += 1

                    self.log.info("[%s] ✓ %d variante(s) enregistrée(s)", ref, len(products))

                except Exception as exc:
                    log_exception(self.log, exc, f"Référence {ref}")

        except Exception as exc:
            log_exception(self.log, exc, "run() setin_by_refs")
        finally:
            if self._db_conn is not None:
                try:
                    self._db_conn.close()
                except Exception:
                    pass
                self._db_conn = None
            await self.close()

        self.log.info("Terminé — %d variante(s) enregistrée(s) en MariaDB", rows_written)


def create_scraper(refs: list[str]) -> SetinByRefsScraper:
    """Factory attendue par la GUI."""
    return SetinByRefsScraper(refs=refs)
