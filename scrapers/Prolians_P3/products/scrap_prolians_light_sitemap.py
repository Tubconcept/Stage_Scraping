"""
Scraper Prolians (mode : catalogue léger COMPLET via sitemap + GraphQL).

Énumère TOUT le catalogue via les sitemaps XML (~78 900 produits) et extrait le
token d'URL de chaque fiche : c'est la « reference » GraphQL, qu'elle soit
numérique (« 10013933 ») ou un Code P encodé (« 03HSWTE »). Puis enrichit en lots
via ``productsByReferences`` (réf, nom, marque, prix, stock, catégorie,
conditionnement) — en **minutes**, sans charger une seule page produit.

Réutilise INTÉGRALEMENT ``ProlianLightScraper`` (login, amorçage GraphQL, fetch
batché, persistance UPDATE-or-INSERT) ; seule la SOURCE des références change :
sitemap exhaustif au lieu du crawl catégories incomplet — voir ``_get_refs``.

Pour les fiches complètes (description, images, EAN, déclinaisons), faire une 2ᵉ
passe DOM via ``scrap_prolians_by_sitemap``.

Factory GUI : ``create_scraper() -> ProlianLightSitemapScraper``.
CLI         : ``python scrap_prolians_light_sitemap.py [--limit N]``.
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
from scrapers.Prolians_P3.products.scrap_prolians_light import ProlianLightScraper

log = setup_logger("prolians.light")


def _url_token(url: str) -> str:
    """Token de fin d'URL = la référence GraphQL (« …-03hswte » → « 03HSWTE »)."""
    return url.rstrip("/").rsplit("-", 1)[-1].upper()


class ProlianLightSitemapScraper(ProlianLightScraper):
    """Catalogue léger COMPLET : références issues du sitemap, enrichies GraphQL."""

    def __init__(self, limit: int | None = None) -> None:
        super().__init__(category_name="", limit=limit)

    def _get_refs(self, page) -> list[str]:
        # Sitemap public (HTTP) : pas besoin du navigateur. Token d'URL = réf GraphQL.
        urls = collect_product_urls(logger=log)
        refs = list(dict.fromkeys(_url_token(u) for u in urls if u))
        log.info("Sitemap : %d référence(s) à enrichir (GraphQL)", len(refs))
        return refs


def create_scraper() -> ProlianLightSitemapScraper:
    """Fabrique attendue par la GUI."""
    return ProlianLightSitemapScraper()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Catalogue léger COMPLET Prolians (sitemap + GraphQL).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limiter le nombre de références (test).")
    args = parser.parse_args()
    ProlianLightSitemapScraper(limit=args.limit)._sync_run()


if __name__ == "__main__":
    main()
