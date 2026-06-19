"""
Scraper Prolians (mode : MAJ prix / stock via GraphQL).

Rafraîchit rapidement le prix, le stock, la disponibilité et le conditionnement
des produits **déjà présents en base**, sans recharger les fiches DOM.

Principe :
  1. Login Playwright (session authentifiée) ;
  2. Amorçage du client GraphQL (capture en-têtes + requêtes — cf.
     ``prolians_graphql``) ;
  3. Lecture des références fournisseur en base (``get_product_references``) ;
  4. Appels batchés ``productsByReferences`` (100 réfs / requête) ;
  5. ``update_product_fields`` ciblé (n'écrase QUE prix/stock/conditionnement/
     promotion — description, images, etc. sont préservées).

~250× plus rapide que le mode DOM pour ces champs. Données propres au point de
vente de la session (même périmètre que ce que voit l'utilisateur connecté).

Factory GUI : ``create_scraper() -> ProlianPriceStockScraper``.
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
from db.mariadb_db import init_site_db, get_product_references, update_product_fields
from scrapers.Prolians_P3.products.prolians_graphql import (
    ProliansGraphQL, price_stock_fields, BATCH_SIZE,
)
from core.logger import setup_logger

log = setup_logger("prolians.price_stock")

load_dotenv(PROJECT_ROOT / ".env")
USERNAME = os.getenv("User_P3")
PASSWORD = os.getenv("Password_P3")


class ProlianPriceStockScraper:
    """MAJ prix/stock par lots GraphQL pour les produits déjà en base."""

    def __init__(self, limit: int | None = None) -> None:
        self._limit = limit
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    async def run(self) -> None:
        await asyncio.to_thread(self._sync_run)

    def _sync_run(self) -> None:
        db_conn = None
        try:
            db_conn = init_site_db("prolians")
        except Exception as exc:
            log.error("Base MariaDB Prolians non initialisée : %s", exc)
            return

        refs = get_product_references(db_conn, "prolians")
        if self._limit:
            refs = refs[: self._limit]
        if not refs:
            log.warning("Aucune référence en base — rien à mettre à jour.")
            return
        log.info("MAJ prix/stock : %d référence(s) à rafraîchir", len(refs))

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

            updated = missing = 0
            for i in range(0, len(refs), BATCH_SIZE):
                if self._stop_requested:
                    log.info("Arrêt demandé.")
                    break
                chunk = refs[i:i + BATCH_SIZE]
                data = gql.fetch(context, chunk, with_analytics=False)
                for ref in chunk:
                    p_data = data.get(ref)
                    if not p_data:
                        missing += 1
                        continue
                    if update_product_fields(None, "prolians", ref, price_stock_fields(p_data)):
                        updated += 1
                log.info("  lot %d-%d : %d MAJ cumulées (%d sans données)",
                         i + 1, i + len(chunk), updated, missing)

            browser.close()
            if db_conn:
                db_conn.close()
            log.info("Terminé : %d produit(s) mis à jour, %d sans données API", updated, missing)


def create_scraper() -> ProlianPriceStockScraper:
    """Factory attendue par la GUI."""
    return ProlianPriceStockScraper()


def main() -> None:
    parser = argparse.ArgumentParser(description="MAJ prix/stock Prolians via GraphQL")
    parser.add_argument("--limit", type=int, default=None, help="Limiter le nombre de références (test)")
    args = parser.parse_args()
    ProlianPriceStockScraper(limit=args.limit)._sync_run()


if __name__ == "__main__":
    main()
