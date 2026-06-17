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
from db.mariadb_db import init_site_db, upsert_product, resolve_decli_index

try:
    from .scraper_legallais_products import LegallaisScraper
    from .scrap_legallais_products import _map_to_csv_headers, _session_perdue, _to_full_url
except ImportError:
    from scrapers.Legallais_P1.products.scraper_legallais_products import LegallaisScraper  # type: ignore[no-redef]
    from scrapers.Legallais_P1.products.scrap_legallais_products import (  # type: ignore[no-redef]
        _map_to_csv_headers, _session_perdue, _to_full_url,
    )


# ─── Helpers _search_and_scrape ───────────────────────────────────────────────

def _find_exact_match(ref_spans: list, result_links: list, ref) -> str | None:
    """Return the URL of the span whose text exactly matches ref, or None."""
    ref_str = str(ref)
    for i, span in enumerate(ref_spans):
        try:
            if span.text.strip() == ref_str and i < len(result_links):
                return _to_full_url(result_links[i].get_attribute("href"))
        except Exception:
            continue
    return None


def _find_product_url(driver, ref) -> str | None:
    """Type ref in the search bar and return the best-matching product URL.

    Prints a diagnostic message and returns None when no URL can be resolved.
    """
    driver.wait_for_element(SELECTORS["search_input"], 3)
    driver.clear(SELECTORS["search_input"], 1)
    driver.type(SELECTORS["search_input"], str(ref), 1)

    try:
        driver.wait_for_element(SELECTORS["search_result"], 3)
    except Exception:
        print(f"   Aucun résultat pour : {ref}")
        return None

    result_links = driver.select_all(SELECTORS["search_result"])
    ref_spans    = driver.select_all(SELECTORS["ref_result"], 1)

    url = _find_exact_match(ref_spans, result_links, ref)
    if url:
        return url
    if result_links:
        return _to_full_url(result_links[0].get_attribute("href"))
    print(f"   Référence introuvable : {ref}")
    return None


def _handle_combo_index(rows: list) -> None:
    """Assign a combinationIndex to all rows when the product is a combination."""
    if rows[0].get("isCombination") != "True":
        return
    parent_ref = rows[0].get("parentRef", "")
    try:
        grp_idx = resolve_decli_index("legallais", parent_ref)
        for _r in rows:
            _r["combinationIndex"] = grp_idx
    except Exception:
        pass


def _upsert_rows(db_conn, rows: list, product_url: str) -> None:
    """Persist each row to MariaDB via upsert, skipping on individual errors."""
    if not db_conn:
        return
    for row in rows:
        try:
            mapped = _map_to_csv_headers(row, "", "", "")
            mapped["product_fournisseur_url"] = product_url
            upsert_product(db_conn, "legallais", mapped)
        except Exception as exc:
            print(f"   MariaDB ignoré : {exc}")


def _scrape_ref(scraper, driver, db_conn, ref, count: int) -> int | None:
    """Process one reference: find URL, scrape, persist.

    Returns updated count, or None if the session is lost (caller must stop).
    """
    try:
        product_url = _find_product_url(driver, ref)
        if not product_url:
            return count

        driver.get(product_url)
        rows = scraper.scrape_product()
        if not rows:
            print(f"   Aucune donnée extraite : {product_url}")
            return count

        _handle_combo_index(rows)
        _upsert_rows(db_conn, rows, product_url)

        count += 1
        ref_found = rows[0].get("productRef", ref)
        print(f"   ✓ {ref_found} — {len(rows)} ligne(s)")
        return count

    except Exception as exc:
        if _session_perdue(exc):
            print(f" Session perdue — arrêt ({exc})")
            return None
        print(f"   Erreur {ref} : {exc}")
        return count


# ─── Session principale ───────────────────────────────────────────────────────

@browser(headless=False)
def _search_and_scrape(driver: Driver, data: dict | None = None) -> None:
    """Session unique : recherche + navigation + extraction pour chaque référence."""
    refs: list[str] = (data or {}).get("refs", [])
    stop_fn         = (data or {}).get("stop_fn", None)

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
        if stop_fn and stop_fn():
            print(" Arrêt demandé — fin de la mise à jour par références.")
            break
        print(f" [{idx}/{total}] Référence : {ref}")
        result = _scrape_ref(scraper, driver, db_conn, ref, count)
        if result is None:
            break
        count = result
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
    _allow_ctypes = True  # close() ferme le driver pool avant l'injection ctypes

    def __init__(self, refs: list[str]) -> None:
        self._refs = refs
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def close(self) -> None:
        try:
            _search_and_scrape.close()
        except Exception:
            pass

    def run(self) -> None:
        _search_and_scrape({"refs": self._refs, "stop_fn": lambda: self._stop_requested})  # type: ignore[call-arg]


def create_scraper(refs: list[str]) -> LegallaisByRefsScraper:
    """Factory attendue par la GUI."""
    return LegallaisByRefsScraper(refs=refs)
