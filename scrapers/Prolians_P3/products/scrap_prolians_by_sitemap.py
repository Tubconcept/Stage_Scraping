"""
Scraper Prolians (mode : catalogue COMPLET via sitemap → fiches DOM).

Énumère TOUTES les fiches produit du catalogue depuis les sitemaps XML
(``prolians_sitemap`` — ~78 900 produits, source autoritaire et exhaustive, là
où le crawl par catégories laisse des trous), puis scrape chaque fiche en DOM
complet (description, images, EAN, déclinaisons…) via ``extract_product_from_dom``.

Réutilise INTÉGRALEMENT ``ProlianByCategoryScraper`` : login, ``_scrape_product``
(chargement durci + garde-fou de session), persistance MariaDB, reprise sur les
URLs déjà en base. Seule la SOURCE des URLs change — voir ``_get_product_urls``.

Pour un catalogue léger/rapide (sans fiche complète), préférer le mode GraphQL
``scrap_prolians_light``.

Factory GUI : ``create_scraper() -> ProlianBySitemapScraper``.
CLI         : ``python scrap_prolians_by_sitemap.py [--limit N]``.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse

from core.logger import setup_logger
from scrapers.Prolians_P3.products.prolians_sitemap import collect_product_urls
from scrapers.Prolians_P3.products.scrap_prolians_by_category import ProlianByCategoryScraper

log = setup_logger("prolians.products")


class ProlianBySitemapScraper(ProlianByCategoryScraper):
    """Scrape les fiches DOM de TOUT le catalogue, énuméré via les sitemaps XML."""

    def __init__(self, limit: int | None = None) -> None:
        # category_name="" → pas de confinement ; la vraie source est le sitemap.
        super().__init__(category_name="", limit=limit)

    def _get_product_urls(self, page) -> list[str]:
        # Le sitemap est public (HTTP simple) : pas besoin du navigateur ici.
        urls = collect_product_urls(logger=log)
        log.info("Sitemap : %d URL(s) produit à scraper", len(urls))
        return urls


def create_scraper() -> ProlianBySitemapScraper:
    """Fabrique attendue par la GUI."""
    return ProlianBySitemapScraper()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraper Prolians — catalogue complet via sitemap (fiches DOM).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limiter le nombre de produits (test).")
    args = parser.parse_args()
    ProlianBySitemapScraper(limit=args.limit)._sync_run()


if __name__ == "__main__":
    main()
