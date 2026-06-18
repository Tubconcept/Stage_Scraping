"""
Orchestrateur du scraper produits Sider (site P6).

Rôle :
    Point d'entrée officiel pour l'extraction du catalogue produits Sider.
    Coordonne le navigateur Playwright, la session utilisateur, la boucle de
    scraping et la persistance en base MariaDB (sider).

Type : produits (catalogue).

Architecture :
    - scrap_sider_products.py (ce fichier) = orchestrateur : run(), persistance,
      gestion session, boucle catégorie/produit, reprise sur crash.
    - scraper_sider_products.py = moteur CSS : sélecteurs, navigation, extraction.

Note : « sider » doit être enregistré dans db/mariadb_db.py (tables produits)
avant la première exécution, sur le même modèle que « setin » ou « prolians ».

Consommateurs : GUI (create_scraper), CLI (python scrap_sider_products.py).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from css_selectors.sider import Selectors
from core.config import PROFILES_DIR
from core.logger import log_exception
from db.mariadb_db import init_site_db, insert_product, get_scraped_product_urls, resolve_decli_index

try:
    from .scraper_sider_products import SiderProductScraper as _SiderCSS
except ImportError:
    from scraper_sider_products import SiderProductScraper as _SiderCSS  # type: ignore[no-redef]


class SiderProductScraper(_SiderCSS):
    """Orchestrateur produits Sider — persistance MariaDB, reprise, session."""

    def __init__(self) -> None:
        super().__init__()
        self._db_conn = None
        self._category_name: str | None = None

    # ─── Persistance MariaDB ──────────────────────────────────────────────────

    def _persist_product(
        self,
        product: dict,
        cat1: str,
        cat2: str,
        cat3: str,
        source_url: str,
    ) -> None:
        """Convertit le dict interne en ligne CSV_HEADERS et insère en MariaDB."""
        row = self.to_csv_row(product, cat1, cat2, cat3, source_url)
        if self._db_conn is not None:
            try:
                insert_product(self._db_conn, "sider", row)
            except Exception as exc:
                self.log.debug("MariaDB produit ignoré : %s", exc)

    # ─── Index de déclinaison ────────────────────────────────────────────────

    def _apply_combination_index(self, products: list[dict]) -> None:
        """Assigne combination_index aux variantes d'un groupe via MariaDB."""
        if not products:
            return
        parent_ref = products[0].get("parent_ref", "") or ""
        try:
            grp_idx = resolve_decli_index("sider", parent_ref)
            for p in products:
                if p.get("is_combination"):
                    p["combination_index"] = grp_idx
        except Exception:
            pass

    # ─── Scrape + persist ────────────────────────────────────────────────────

    async def _scrape_and_persist(
        self,
        page,
        url: str,
        cat1: str,
        cat2: str,
        cat3: str,
    ) -> int:
        """Extrait un produit (et ses variantes), applique les index, persiste.

        Returns:
            Nombre de lignes effectivement insérées.
        """
        has_combo, products = await self._get_product_data(page, url)
        if has_combo and products:
            self._apply_combination_index(products)
        for product in products:
            self._persist_product(product, cat1, cat2, cat3, url)
        return len(products)

    # ─── Session ─────────────────────────────────────────────────────────────

    async def _ensure_session(self, page, storage_path: Path) -> None:
        """Navigue vers l'accueil, accepte les cookies et se connecte si besoin.

        Après connexion, retourne sur BASE_URL pour que _get_categories()
        trouve le menu dans son état initial.
        """
        await page.goto(Selectors.BASE_URL)
        await page.wait_for_load_state("domcontentloaded")
        await self._accept_cookies(page)
        await self._connexion(page)
        await self.save_storage_state(str(storage_path))

        if page.url != Selectors.BASE_URL:
            await page.goto(Selectors.BASE_URL)
            await page.wait_for_load_state("domcontentloaded")

    # ─── Reprise sur crash ───────────────────────────────────────────────────

    def _load_seen_urls(self) -> set[str]:
        """Charge les URLs déjà scrapées depuis MariaDB pour reprendre un run interrompu."""
        seen: set[str] = (
            get_scraped_product_urls(self._db_conn, "sider")
            if self._db_conn is not None else set()
        )
        if seen:
            self.log.info("Reprise — %d URL(s) déjà scrapée(s) ignorées", len(seen))
        return seen

    # ─── Boucle catalogue ────────────────────────────────────────────────────

    async def _process_category(
        self,
        cat_page,
        product_page,
        cat_url: str,
        seen: set[str],
        limit: int | None,
        rows_written: int,
    ) -> int:
        """Collecte les liens d'une sous-catégorie et scrape chaque produit.

        Utilise deux pages distinctes : cat_page pour naviguer dans le catalogue
        (pagination), product_page pour les fiches produit et leurs variantes.

        Returns:
            Total cumulatif de lignes écrites.
        """
        product_links = await self._get_product_links(cat_page, cat_url)
        cat1, cat2, cat3 = await self._ariane(cat_page)
        self.log.info(
            "Catégorie [%s > %s > %s] — %d produit(s)",
            cat1, cat2, cat3, len(product_links),
        )

        for url in product_links:
            if self.should_stop():
                break
            if limit is not None and rows_written >= limit:
                break
            if url in seen:
                continue
            seen.add(url)
            try:
                rows_written += await self._scrape_and_persist(
                    product_page, url, cat1, cat2, cat3
                )
            except Exception as exc:
                log_exception(self.log, exc, f"produit {url}")

        return rows_written

    async def _run_catalogue(
        self, page, seen: set[str], limit: int | None,
        category_name: str | None = None,
    ) -> int:
        """Parcourt les sous-catégories du menu et scrape leurs produits.

        category_name : texte exact d'une entrée niveau 1 du megamenu Sider
            (ex: "OUTILLAGE"). None ou "Toutes les catégories" = tout le catalogue.

        Returns:
            Nombre total de lignes écrites.
        """
        cat_urls = await self._get_categories(page, category_name)
        self.log.info("%d sous-catégorie(s) à traiter", len(cat_urls))

        product_page = await self.new_page()
        rows_written = 0

        for cat_url in cat_urls:
            if self.should_stop():
                break
            try:
                rows_written = await self._process_category(
                    page, product_page, cat_url, seen, limit, rows_written
                )
            except Exception as exc:
                log_exception(self.log, exc, f"catégorie {cat_url}")

        return rows_written

    # ─── Point d'entrée ──────────────────────────────────────────────────────

    async def run(self, limit_products: int | None = None) -> None:
        """Lance le scraping complet : session → menu → produits → MariaDB."""
        try:
            self._db_conn = init_site_db("sider")
        except Exception as exc:
            self.log.warning("Base MariaDB non initialisée : %s", exc)
            self._db_conn = None

        storage_path = PROFILES_DIR / "sider" / "session.json"
        storage_state = str(storage_path) if storage_path.exists() else None

        await self.start_browser(headless=False, storage_state=storage_state)
        page = await self.new_page()
        rows_written = 0

        try:
            await self._ensure_session(page, storage_path)
            seen = self._load_seen_urls()
            rows_written = await self._run_catalogue(
                page, seen, limit_products, self._category_name
            )
        except Exception as exc:
            log_exception(self.log, exc, "run() sider_products")
        finally:
            if self._db_conn is not None:
                try:
                    self._db_conn.close()
                except Exception:
                    pass
                self._db_conn = None
            await self.close()

        self.log.info("Terminé — %d produit(s) enregistré(s) en MariaDB", rows_written)


# ─── Factory GUI / CLI ────────────────────────────────────────────────────────

def create_scraper(category_name: str | None = None) -> SiderProductScraper:
    """Factory attendue par la GUI (gui/interface.py)."""
    scraper = SiderProductScraper()
    scraper._category_name = category_name or None
    return scraper


async def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper produits Sider → MariaDB")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limite de variantes à scraper (utile pour tests)"
    )
    parser.add_argument(
        "--category", type=str, default=None,
        help="Catégorie niveau 1 à scraper (ex: 'OUTILLAGE'). Vide = toutes."
    )
    args = parser.parse_args()

    limit = args.limit
    if limit is None and os.getenv("SIDER_PRODUCT_LIMIT", "").isdigit():
        limit = int(os.getenv("SIDER_PRODUCT_LIMIT", ""))

    scraper = create_scraper(category_name=args.category)
    await scraper.run(limit_products=limit)


if __name__ == "__main__":
    asyncio.run(main())
