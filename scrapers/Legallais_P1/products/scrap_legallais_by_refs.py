"""Scraper Legallais — mode mise à jour par liste de références (P1).

Pour chaque référence fournie :
  1. Tape la référence dans la barre de recherche Legallais.
  2. Si des résultats apparaissent, cherche une correspondance exacte ;
     à défaut, prend le premier résultat.
  3. Navigue directement vers la fiche produit.
  4. Extrait toutes les données via LegallaisScraper.scrape_product().
  5. Insère dans MariaDB (mêmes tables, mêmes règles que le mode catalogue).

Tout s'exécute dans une seule session Botasaurus — pas de double passage.

Factory attendue par la GUI :
    create_scraper(refs: list[str]) → LegallaisByRefsScraper
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from botasaurus.browser import Driver, browser

from css_selectors.legallais import BASE_URL, SELECTORS
from db.mariadb_db import init_site_db, upsert_product

try:
    from .scraper_legallais_products import LegallaisScraper
    from .scrap_legallais_products import _map_to_csv_headers, _session_perdue, _to_full_url
except ImportError:
    from scrapers.Legallais_P1.products.scraper_legallais_products import LegallaisScraper  # type: ignore[no-redef]
    from scrapers.Legallais_P1.products.scrap_legallais_products import (  # type: ignore[no-redef]
        _map_to_csv_headers, _session_perdue, _to_full_url,
    )


@browser(headless=False)
def _search_and_scrape(driver: Driver, data: dict | None = None) -> None:
    """Session unique : recherche + navigation + extraction pour chaque référence."""
    refs: list[str] = (data or {}).get("refs", [])

    scraper = LegallaisScraper()
    scraper.set_driver(driver)
    scraper.connexion()

    db_conn = None
    try:
        db_conn = init_site_db("legallais")
    except Exception as exc:
        print(f" Base MariaDB non initialisée : {exc}")

    driver.get(BASE_URL)
    total = len(refs)
    count = 0

    for idx, ref in enumerate(refs, 1):
        print(f" [{idx}/{total}] Référence : {ref}")
        try:
            # ── Recherche dans la barre ──────────────────────────────────────
            driver.wait_for_element(SELECTORS["search_input"], 3)
            driver.clear(SELECTORS["search_input"], 1)
            driver.type(SELECTORS["search_input"], str(ref), 1)

            try:
                driver.wait_for_element(SELECTORS["search_result"], 3)
            except Exception:
                print(f"   Aucun résultat pour : {ref}")
                driver.get(BASE_URL)
                continue

            result_links = driver.select_all(SELECTORS["search_result"])
            ref_spans    = driver.select_all(SELECTORS["ref_result"], 1)

            product_url: str | None = None

            # Chercher correspondance exacte sur la référence
            for i, span in enumerate(ref_spans):
                try:
                    if span.text.strip() == str(ref) and i < len(result_links):
                        href = result_links[i].get_attribute("href")
                        product_url = _to_full_url(href)
                        break
                except Exception:
                    continue

            # Pas de correspondance exacte → premier résultat
            if product_url is None and result_links:
                href = result_links[0].get_attribute("href")
                product_url = _to_full_url(href)

            if not product_url:
                print(f"   Référence introuvable : {ref}")
                driver.get(BASE_URL)
                continue

            # ── Navigation vers la fiche produit ────────────────────────────
            driver.get(product_url)

            # ── Extraction des données ───────────────────────────────────────
            rows = scraper.scrape_product()
            if not rows:
                print(f"   Aucune donnée extraite : {product_url}")
                driver.get(BASE_URL)
                continue

            if db_conn:
                for row in rows:
                    try:
                        mapped = _map_to_csv_headers(row, "", "", "")
                        mapped["product_fournisseur_url"] = product_url
                        upsert_product(db_conn, "legallais", mapped)
                    except Exception as exc:
                        print(f"   MariaDB ignoré : {exc}")

            count += 1
            ref_found = rows[0].get("productRef", ref)
            print(f"   ✓ {ref_found} — {len(rows)} ligne(s)")

        except Exception as exc:
            if _session_perdue(exc):
                print(f" Session perdue — arrêt ({exc})")
                break
            print(f"   Erreur {ref} : {exc}")

        # Retour à l'accueil pour la prochaine recherche
        try:
            driver.get(BASE_URL)
        except Exception:
            pass

    if db_conn:
        try:
            db_conn.close()
        except Exception:
            pass

    print(f"\n {count}/{total} produit(s) enregistré(s) en MariaDB")


class LegallaisByRefsScraper:
    """Scrape une liste ciblée de références — session Botasaurus unique.

    run() est synchrone (Botasaurus) — doit être appelé dans un thread via
    _start_sync() de la GUI.
    """

    def __init__(self, refs: list[str]) -> None:
        self._refs = refs
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        _search_and_scrape({"refs": self._refs})  # type: ignore[call-arg]


def create_scraper(refs: list[str]) -> LegallaisByRefsScraper:
    """Factory attendue par la GUI."""
    return LegallaisByRefsScraper(refs=refs)
