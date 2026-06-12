"""Scraper Prolians — mode mise à jour par liste de références (P3).

Pour chaque référence fournie :
  1. Navigue vers l'URL de recherche Prolians (search?q={ref}).
  2. Si la page redirige directement vers une fiche produit → scrape.
  3. Sinon, cherche le premier résultat dont la référence correspond exactement ;
     à défaut, prend le premier résultat disponible.
  4. Extrait toutes les données via extract_product_from_dom (même logique
     que le mode catalogue / sitemaps).
  5. Insère dans MariaDB (mêmes tables, mêmes règles).

Factory attendue par la GUI :
    create_scraper(refs: list[str]) → ProlianByRefsScraper
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import quote

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from auth.prolians.cookie_manager import ensure_logged_in, is_logged_in
from css_selectors.prolians import Selectors
from db.mariadb_db import init_site_db, upsert_product, resolve_decli_index
from scrapers.Prolians_P3.products.scraper_prolians_products import extract_product_from_dom

load_dotenv(_PROJECT_ROOT / ".env")

USERNAME = os.getenv("User_P3")
PASSWORD = os.getenv("Password_P3")


class ProlianByRefsScraper:
    """Scrape une liste ciblée de références via la recherche Prolians.

    run() est async (asyncio.to_thread) pour s'intégrer à la boucle asyncio
    de la GUI, mais le travail Playwright s'exécute de façon synchrone dans
    un thread dédié — même pattern que ProlianProductScraper.
    """

    def __init__(self, refs: list[str]) -> None:
        self._refs = refs
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
            print(f" Base MariaDB Prolians non initialisée : {exc}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            context.set_default_timeout(8000)
            context.set_default_navigation_timeout(20000)
            page = context.new_page()

            if not ensure_logged_in(page, context, USERNAME, PASSWORD):
                browser.close()
                if db_conn:
                    db_conn.close()
                return

            count = 0
            print(f" Mode références Prolians : {len(self._refs)} référence(s)")

            for idx, ref in enumerate(self._refs):
                if self._stop_requested:
                    print("\n Arrêt demandé.")
                    break

                search_url = f"{Selectors.search_url_template}{quote(str(ref))}"
                print(f" [{idx + 1}/{len(self._refs)}] Recherche : {ref}")

                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
                    current_url = page.url

                    # Vérifier si redirection directe vers la fiche produit
                    is_product = (
                        "/product/" in current_url
                        or "/p/" in current_url
                        or "search" not in current_url
                    )

                    if not is_product:
                        # Page de résultats : chercher la référence exacte en premier
                        product_href = None
                        try:
                            page.wait_for_selector(Selectors.search_result_link, timeout=6000)
                            result_links = page.locator(Selectors.search_result_link).all()
                            ref_spans    = page.locator(Selectors.search_result_ref).all()

                            for i, span in enumerate(ref_spans):
                                try:
                                    if span.inner_text(timeout=2000).strip() == str(ref):
                                        product_href = result_links[i].get_attribute("href")
                                        break
                                except Exception:
                                    continue

                            # Si aucune correspondance exacte → 1er résultat
                            if product_href is None and result_links:
                                product_href = result_links[0].get_attribute("href")

                        except Exception:
                            print(f"   Résultats introuvables pour : {ref}")
                            continue

                        if not product_href:
                            print(f"   Aucun résultat pour : {ref}")
                            continue

                        full_url = (
                            product_href
                            if product_href.startswith("http")
                            else f"{Selectors.BASE_URL}{product_href}"
                        )
                        page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
                        current_url = page.url

                    # Contrôle de session périodique
                    if count % 20 == 0 and not is_logged_in(page):
                        if not ensure_logged_in(page, context, USERNAME, PASSWORD):
                            break
                        page.goto(current_url, wait_until="domcontentloaded", timeout=10000)

                    # Extraction complète (même fonction que le mode catalogue)
                    try:
                        page.wait_for_selector(Selectors.title, timeout=5000)
                    except Exception:
                        print(f"   Fiche non chargée pour : {ref}")
                        continue

                    rows = extract_product_from_dom(page)
                    if not rows:
                        print(f"   Aucune donnée extraite pour : {ref}")
                        continue
                    if any(r.get("products_is_combination") == "True" for r in rows):
                        parent_ref = rows[0].get("product_parent_reference", "")
                        try:
                            grp_idx = resolve_decli_index("prolians", parent_ref)
                            for row in rows:
                                if row.get("products_is_combination") == "True":
                                    row["product_combination_index"] = grp_idx
                        except Exception:
                            pass

                    if db_conn:
                        for row in rows:
                            try:
                                db_row = dict(row)
                                db_row["product_fournisseur_url"] = current_url
                                upsert_product(db_conn, "prolians", db_row)
                            except Exception:
                                pass

                    count += 1
                    ref_found = rows[0].get("product_reference_fournisseur", ref)
                    print(f"   [{count}] {ref_found} ({len(rows)} ligne(s))")

                except Exception as exc:
                    print(f"   Erreur référence {ref} : {exc}")

            browser.close()
            if db_conn:
                db_conn.close()
            print(f"\n {count} produit(s) enregistré(s) en MariaDB")


def create_scraper(refs: list[str]) -> ProlianByRefsScraper:
    """Factory attendue par la GUI."""
    return ProlianByRefsScraper(refs=refs)
