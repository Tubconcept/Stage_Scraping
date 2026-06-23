"""
Scraper Prolians (mode : catalogue léger via GraphQL).

Construit/rafraîchit rapidement un catalogue avec les champs disponibles par
l'API (réf, nom, marque, prix, catégorie, conditionnement, parent, URL),
**sans** charger les fiches DOM. Idéal pour un inventaire rapide quand on n'a
pas besoin de description / images / documents / caractéristiques / déclinaisons.

Principe :
  1. Login + amorçage du client GraphQL ;
  2. Parcours de l'arbre des catégories (réutilise ``ProlianByCategoryScraper``)
     → collecte des **références** (confiné à une catégorie ou catalogue complet) ;
  3. Appels batchés ``productsByReferences`` (prix/stock + nom/marque/catégorie) ;
  4. Persistance : ``update_product_fields`` si la réf existe (préserve les
     champs riches), sinon ``insert_product`` (nouvelle ligne légère).

Pour une fiche complète (description, images, EAN, déclinaisons…), utiliser le
mode DOM ``scrap_prolians_by_category``.

Factory GUI : ``create_scraper(category_name="") -> ProlianLightScraper``.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import asyncio
import os

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from auth.prolians.cookie_manager import ensure_logged_in
from css_selectors.prolians import Selectors
from db.mariadb_db import init_site_db, insert_product, update_product_fields
from scrapers.Prolians_P3.products.prolians_graphql import (
    ProliansGraphQL, light_row, BATCH_SIZE,
)
from scrapers.Prolians_P3.products.scrap_prolians_by_category import ProlianByCategoryScraper
from core.logger import setup_logger

log = setup_logger("prolians.light")

load_dotenv(PROJECT_ROOT / ".env")
USERNAME = os.getenv("User_P3")
PASSWORD = os.getenv("Password_P3")


class ProlianLightScraper:
    """Catalogue léger : crawl catégorie pour les réfs + enrichissement GraphQL."""

    def __init__(self, category_name: str = "", limit: int | None = None) -> None:
        name = (category_name or "").strip()
        if name == Selectors.ALL_CATEGORIES_LABEL:
            name = ""
        self._category_name = name
        self._limit = limit
        self._stop_requested = False
        # Réutilise le crawler par catégorie pour le BFS / le confinement
        self._crawler = ProlianByCategoryScraper(category_name=category_name)

    def request_stop(self) -> None:
        self._stop_requested = True
        self._crawler.request_stop()

    async def run(self) -> None:
        await asyncio.to_thread(self._sync_run)

    def _get_refs(self, page) -> list[str]:
        """Source des références à enrichir via GraphQL. Par défaut : parcours de
        l'arbre catégories (confiné ou complet). Surchargée par le mode sitemap
        (``ProlianLightSitemapScraper``) pour énumérer le catalogue COMPLET."""
        seeds, confine = self._crawler._seed_categories(page)
        if not seeds:
            return []
        if confine:
            log.info("Périmètre : « %s » — confiné à %s", self._category_name, confine)
        else:
            log.warning("Périmètre : CATALOGUE COMPLET — %d catégorie(s) principale(s)", len(seeds))
        return self._crawler._crawl_product_refs(page, seeds, confine)

    def _persist(self, row: dict) -> str:
        """UPDATE si la réf existe (préserve les champs riches), sinon INSERT.

        Retourne 'maj' ou 'new'.
        """
        ref = row.get("product_reference_fournisseur", "")
        if update_product_fields(None, "prolians", ref, row):
            return "maj"
        try:
            insert_product(None, "prolians", row)
        except Exception:
            pass
        return "new"

    def _sync_run(self) -> None:
        db_conn = None
        try:
            db_conn = init_site_db("prolians")
        except Exception as exc:
            log.error("Base MariaDB Prolians non initialisée : %s", exc)
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            context.set_default_timeout(8000)
            context.set_default_navigation_timeout(20000)
            page = context.new_page()

            if not ensure_logged_in(page, context, USERNAME, PASSWORD):
                log.error("Connexion Prolians échouée — arrêt.")
                browser.close()
                return

            gql = ProliansGraphQL()
            if not gql.prime(page):
                log.error("Amorçage GraphQL impossible — arrêt.")
                browser.close()
                return

            # 1) Source des références (catégories par défaut ; sitemap en surcharge)
            refs = self._get_refs(page)
            if self._limit:
                refs = refs[: self._limit]
            if not refs:
                log.warning("Aucune référence collectée — arrêt.")
                browser.close()
                return

            # 2) Enrichissement GraphQL batché + persistance
            maj = new = missing = 0
            for i in range(0, len(refs), BATCH_SIZE):
                if self._stop_requested:
                    log.info("Arrêt demandé.")
                    break
                chunk = refs[i:i + BATCH_SIZE]
                data = gql.fetch(context, chunk, with_analytics=True)
                for ref in chunk:
                    p_data = data.get(ref)
                    if not p_data:
                        missing += 1
                        continue
                    outcome = self._persist(light_row(p_data))
                    if outcome == "maj":
                        maj += 1
                    else:
                        new += 1
                log.info("  lot %d-%d : %d nouveau(x), %d MAJ, %d sans données",
                         i + 1, i + len(chunk), new, maj, missing)

            browser.close()
            if db_conn:
                db_conn.close()
            log.info("Terminé : %d nouveau(x), %d mis à jour, %d sans données API", new, maj, missing)


def create_scraper(category_name: str = "") -> ProlianLightScraper:
    """Factory attendue par la GUI."""
    return ProlianLightScraper(category_name=category_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Catalogue léger Prolians via GraphQL")
    parser.add_argument("--category", default="", help="Catégorie principale (vide = tout)")
    parser.add_argument("--limit", type=int, default=None, help="Limiter le nombre de références (test)")
    args = parser.parse_args()
    ProlianLightScraper(category_name=args.category, limit=args.limit)._sync_run()


if __name__ == "__main__":
    main()
