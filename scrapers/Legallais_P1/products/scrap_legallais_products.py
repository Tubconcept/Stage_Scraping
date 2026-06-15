"""
Orchestrateur du scraper produits Legallais (site P1).

Rôle :
    Coordonne la collecte des URLs produits (phase 1) et l'extraction parallèle
    ou séquentielle (phase 2), puis persiste les variantes en MariaDB.

Type : produits (catalogue browse ou recherche par références JSON).

Architecture :
    - scrap_legallais_products.py (ce fichier) = orchestrateur : décorateurs
      @browser Botasaurus, mapping CSV_HEADERS, reprise via get_scraped_product_urls().
    - scraper_legallais_products.py = moteur CSS : connexion, menu catégories,
      pagination, extraction scrape_product().

Modes :
    - browse : parcours du menu à 3 niveaux, scraping page par page.
    - search : recherche par référence depuis un fichier JSON (refs.json).

Aucune logique CSS ne doit rester dans ce fichier.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
import re
import argparse
from typing import List, Dict, Optional

from botasaurus.browser import browser, Driver
from core.config import JSON_DIR, CSV_HEADERS
from css_selectors.legallais import BASE_URL, SELECTORS, CATEGORY_NAMES
from core.logger import setup_logger
log = setup_logger("legallais.products")
from db.mariadb_db import init_site_db, insert_product, get_scraped_product_urls, resolve_decli_index

# Fonctionne à la fois en import package (GUI) et en script standalone (CLI)
try:
    from .scraper_legallais_products import LegallaisScraper
except ImportError:
    from scrapers.Legallais_P1.products.scraper_legallais_products import LegallaisScraper  # type: ignore[no-redef]

# Borne haute du décorateur @browser
_MAX_PARALLEL = 8

# Flag d'arrêt partagé entre la classe wrapper et la fonction Botasaurus
_stop_flag: bool = False


# ─── Mapping interne → CSV_HEADERS standardisés ───────────────────────────────

def _map_to_csv_headers(row: dict, cat1: str, cat2: str, cat3: str) -> dict:
    """Mappe les clés internes du scraper vers les CSV_HEADERS standardisés."""
    category_tree = "||".join(c for c in [cat1, cat2, cat3] if c)

    mapped = {
        "product_fournisseur":           "P1",
        "product_reference_fournisseur": row.get("productRef", ""),
        "product_ean":                   row.get("EAN", ""),
        "product_reference_fabricant":   row.get("Ref_fabricant", ""),
        "product_brand":                 row.get("productBrand", ""),
        "product_brand_logo_url":        row.get("Image_Brand", ""),
        "product_designation":           row.get("productTitle", ""),
        "product_description":           row.get("productDesc", ""),
        "product_image_url":             row.get("productImages", ""),
        "product_docs_url":              row.get("productDocList", ""),
        "product_category_tree":         category_tree,
        "product_conditionnement":       row.get("conditionnement", ""),
        "product_stock_status":          row.get("stockStatus", ""),
        "product_status":                row.get("productStatus", ""),
        "product_fournisseur_url":       row.get("ProductUrl", ""),
        "product_eco_label":             row.get("ecoLabel", ""),
        "product_eco_taxe":              row.get("price_eco", ""),
        "product_promotion":             row.get("price_original", ""),
        "product_price_ht":              row.get("productPrice", ""),
        "product_attributes":            row.get("productAttributes", ""),
        "products_is_combination":       str(row.get("isCombination", "False")),
        "product_combination_index":     str(row.get("combinationIndex", "") or ""),
        "product_parent_reference":      row.get("parentRef", "") or row.get("productRef", ""),
        "product_child_reference":       row.get("childRefs", "") or row.get("productRef", ""),
        "product_combination_values":    row.get("productDecliName&Value", ""),
        "product_cross_sell":            row.get("crossSell", ""),
    }
    return {k: str(mapped.get(k, "") or "") for k in CSV_HEADERS}


# ─── UTILITAIRES ──────────────────────────────────────────────────────────────

def _to_full_url(href: str) -> str:
    """Normalise un href relatif ou absolu en URL complète."""
    return href if href.startswith("http") else BASE_URL + href


def _split_batches(items: list, n: int) -> list:
    """Divise une liste en n lots de taille approximativement égale."""
    if not items:
        return []
    n = min(n, len(items))
    q, r = divmod(len(items), n)
    batches, start = [], 0
    for i in range(n):
        end = start + q + (1 if i < r else 0)
        batches.append(items[start:end])
        start = end
    return batches



def _session_perdue(ex: Exception) -> bool:
    """Retourne True si l'exception indique une perte de session Chrome."""
    msg = str(ex).lower()
    return not msg or "target" in msg or "goodbye" in msg or "connection" in msg or "closed" in msg


def _variant_url(base_url: str, variant_ref: str) -> str:
    """Remplace les chiffres finaux du dernier segment de l'URL par la ref de la variante.

    Exemple : ".../wi693165" + "693166" → ".../wi693166"
    Évite les doublons d'URL pour les déclinaisons d'un même produit.
    """
    if not variant_ref:
        return base_url
    last_seg = base_url.rsplit("/", 1)[-1]
    new_last = re.sub(r"\d+$", variant_ref, last_seg)
    if new_last == last_seg:
        return base_url
    return base_url.rsplit("/", 1)[0] + "/" + new_last


# ─── PHASE 1 : collecte des URLs produits (navigateur unique) ─────────────────

@browser(headless=False)
def _collect_browse(driver: Driver, data: dict = None) -> List[Dict]:  # type: ignore[assignment]
    """
    Phase 1 (mode browse) : navigue dans les catégories et collecte toutes les
    URLs produits avec leurs catégories breadcrumb (cat1/cat2/cat3).
    """
    category_filter: Optional[str] = data.get("category_filter")
    scraper = LegallaisScraper()
    scraper.set_driver(driver)
    scraper.connexion()

    driver.get(BASE_URL)
    driver.wait_for_element(".o-menu__items__list")
    driver.move_mouse_to_element("#products-navbar-button")
    driver.click("#products-navbar-button")

    categories = scraper.get_categories(category_filter)
    if not categories:
        log.warning("Aucune catégorie trouvée")
        return []

    categories = [c for c in categories if "guides-de-choix" not in c]
    log.info(f"Collecte de {len(categories)} catégories")
    all_products: List[Dict] = []

    for idx, category_url in enumerate(categories, 1):
        log.info(f"[{idx}/{len(categories)}] Catégorie: {category_url}")
        try:
            driver.get(_to_full_url(category_url))
            scraper.set_items_per_page()

            cat1, cat2, cat3 = scraper.get_breadcrumb_categories()
            total_pages = scraper.get_page_count()
            log.info(f"  {total_pages} page(s) dans cette catégorie")

            for page_num in range(1, total_pages + 1):
                if page_num > 1:
                    scraper.go_to_page(page_num)
                for href in scraper.get_product_links():
                    all_products.append({
                        "url": _to_full_url(href),
                        "cat1": cat1,
                        "cat2": cat2,
                        "cat3": cat3,
                    })

            log.info(f"  Total cumulé: {len(all_products)} produits")
        except Exception as e:
            log.error(f"Erreur collecte catégorie {category_url}: {e}")

    log.info(f"Phase 1 terminée — {len(all_products)} produits collectés")
    return all_products


@browser(headless=False)
def _collect_search(driver: Driver, data: dict = None) -> List[Dict]:  # type: ignore[assignment]
    """
    Phase 1 (mode search) : recherche chaque référence et collecte l'URL produit.
    """
    json_file: str = data.get("json_file", "refs.json")
    scraper = LegallaisScraper()
    scraper.set_driver(driver)
    scraper.connexion()

    with open(JSON_DIR / json_file, "r", encoding="utf-8") as f:
        product_refs = json.load(f)

    driver.get(BASE_URL)
    all_products: List[Dict] = []

    for idx, ref in enumerate(product_refs, 1):
        log.info(f"[{idx}/{len(product_refs)}] Recherche: {ref}")
        try:
            driver.wait_for_element(SELECTORS["search_input"], 1)
            driver.clear(SELECTORS["search_input"], 1)
            driver.type(SELECTORS["search_input"], str(ref).zfill(6), 1)

            try:
                driver.wait_for_element(SELECTORS["search_result"], 1)
            except Exception:
                log.error(f"Aucun résultat pour la ref: {ref}")
                continue

            if not driver.is_element_present(SELECTORS["search_result"]):
                log.warning(f"Résultat absent pour: {ref}")
                continue

            results = driver.select_all(SELECTORS["search_result"])
            ref_results = driver.select_all(SELECTORS["ref_result"], 1)

            product_url = None
            for i, r in enumerate(ref_results):
                if r.text == ref:
                    href = results[i].get_attribute("href")
                    product_url = _to_full_url(href)
                    break

            if product_url:
                all_products.append({"url": product_url, "cat1": "", "cat2": "", "cat3": ""})
                log.info(f"  URL collectée: {product_url}")
            else:
                log.warning(f"Référence exacte non trouvée: {ref}")

        except Exception as e:
            log.error(f"Erreur recherche {ref}: {e}")

    log.info(f"Phase 1 terminée — {len(all_products)} produits collectés")
    return all_products


# ─── PHASE 2 : scraping parallèle ─────────────────────────────────────────────

@browser(headless=True, parallel=_MAX_PARALLEL)
def _scrape_batch(driver: Driver, data: dict) -> None:
    """
    Worker parallèle : reçoit un lot de produits {"url", "cat1", "cat2", "cat3"}.
    """
    batch_id: int      = data["batch_id"]
    products: List[Dict] = data["products"]

    scraper = LegallaisScraper()
    scraper.set_driver(driver)
    scraper.connexion()

    db_conn = None
    try:
        db_conn = init_site_db("legallais")
    except Exception as _exc:
        log.warning(f"[Worker {batch_id}] MariaDB non initialisée : {_exc}")

    log.info(f"[Worker {batch_id}] Démarrage — {len(products)} produits à traiter")
    ok, err = 0, 0

    for item in products:
        try:
            driver.get(item["url"])

            cat1, cat2, cat3 = item["cat1"], item["cat2"], item["cat3"]

            rows = scraper.scrape_product()
            if rows:
                if rows[0].get("isCombination") == "True":
                    parent_ref = rows[0].get("parentRef", "")
                    try:
                        grp_idx = resolve_decli_index("legallais", parent_ref)
                    except Exception:
                        grp_idx = ok + 1
                    for _r in rows:
                        _r["combinationIndex"] = grp_idx
                for row in rows:
                    if db_conn:
                        try:
                            mapped = _map_to_csv_headers(row, cat1, cat2, cat3)
                            is_combo = str(row.get("isCombination", "False")) == "True"
                            variant_ref = row.get("productRef", "") if is_combo else ""
                            mapped["product_fournisseur_url"] = _variant_url(item.get("url", ""), variant_ref)
                            insert_product(db_conn, "legallais", mapped)
                        except Exception as _db_exc:
                            log.debug(f"[Worker {batch_id}] MariaDB ignoré : {_db_exc}")
                ok += 1
                log.info(f"[Worker {batch_id}] ✓ {rows[0].get('productRef','?')} — {len(rows)} ligne(s)")
            else:
                log.warning(f"[Worker {batch_id}] ✗ Aucune donnée pour {item['url']}")
                err += 1
        except Exception as e:
            log.error(f"[Worker {batch_id}] Erreur {item['url']}: {e}")
            err += 1

    if db_conn:
        db_conn.close()

    log.info(f"[Worker {batch_id}] Terminé — {ok} OK, {err} erreurs")


# ─── SCRAPING SÉQUENTIEL ──────────────────────────────────────────────────────

@browser(headless=False)
def _scrape_direct(driver: Driver, data: dict = None) -> None:
    """Scrape séquentiel page par page : pour chaque page de listing, entre dans
    chaque produit, récupère les infos, écrit dans le CSV, puis passe à la page suivante."""
    products: List[Dict]       = data.get("products", [])
    mode: str                  = data.get("mode", "browse")
    category_filter: str | None = data.get("category_filter")

    scraper = LegallaisScraper()
    scraper.set_driver(driver)
    scraper.connexion()

    # Open Legallais DB once for the whole run; seed visited_urls for resume
    db_conn = None
    try:
        db_conn = init_site_db("legallais")
    except Exception as _exc:
        log.warning(f"Base MariaDB Legallais non initialisée : {_exc}")

    if mode == "browse":
        driver.get(BASE_URL)
        driver.wait_for_element(".o-menu__items__list")
        driver.move_mouse_to_element("#products-navbar-button")
        driver.click("#products-navbar-button")
        categories = scraper.get_categories(category_filter)
        categories = [c for c in categories if "guides-de-choix" not in c]
        log.info(f"{len(categories)} catégories trouvées")

        ok, err = 0, 0
        # Seed visited_urls from DB for crash-safe resume
        visited_urls: set = (
            get_scraped_product_urls(db_conn, "legallais") if db_conn else set()
        )
        if visited_urls:
            log.info(f"Reprise — {len(visited_urls)} URL(s) déjà scrappée(s) ignorées")

        for idx, cat_url in enumerate(categories, 1):
            if _stop_flag:
                break
            try:
                full_url = cat_url if cat_url.startswith("http") else BASE_URL + cat_url
                driver.get(full_url)

                scraper.set_items_per_page()
                try:
                    driver.wait_for_element(SELECTORS["breadcrumb"], 5)
                except Exception:
                    pass
                cat1, cat2, cat3 = scraper.get_breadcrumb_categories()
                log.info(f"  Breadcrumb : {cat1} > {cat2} > {cat3}")
                try:
                    driver.wait_for_element(SELECTORS["product_card"], 3)
                except Exception:
                    pass

                total_pages = scraper.get_page_count()
                log.info(
                    f"[{idx}/{len(categories)}] {cat1} > {cat2} > {cat3}"
                    f" — {total_pages} page(s)"
                )

                for page_num in range(1, total_pages + 1):
                    if _stop_flag:
                        break
                    if page_num > 1:
                        scraper.go_to_page(page_num)

                    for href in scraper.get_product_links():
                        if _stop_flag:
                            break
                        try:
                            full_href = href if href.startswith("http") else BASE_URL + href
                            if full_href in visited_urls:
                                continue
                            visited_urls.add(full_href)

                            log.info(f"  → Produit {ok + err + 1} : {full_href}")
                            driver.get(full_href)
                            rows = scraper.scrape_product()
                            if rows and rows[0].get("isCombination") == "True":
                                parent_ref = rows[0].get("parentRef", "")
                                try:
                                    grp_idx = resolve_decli_index("legallais", parent_ref)
                                except Exception:
                                    grp_idx = ok + 1
                                for _r in rows:
                                    _r["combinationIndex"] = grp_idx

                            if rows:
                                for row in rows:
                                    if db_conn:
                                        try:
                                            mapped = _map_to_csv_headers(row, cat1, cat2, cat3)
                                            is_combo = str(row.get("isCombination", "False")) == "True"
                                            variant_ref = row.get("productRef", "") if is_combo else ""
                                            mapped["product_fournisseur_url"] = _variant_url(full_href, variant_ref)
                                            insert_product(db_conn, "legallais", mapped)
                                        except Exception as _db_exc:
                                            log.debug(f"MariaDB produit ignoré : {_db_exc}")
                                ok += 1
                                ref = rows[0].get("productRef", "?")
                                log.info(f"  ✓ [{ok}] {ref} — {len(rows)} ligne(s) écrite(s)")
                            else:
                                err += 1
                                log.warning(f"  ✗ Aucune donnée : {full_href}")

                        except Exception as ex:
                            if _session_perdue(ex):
                                log.error(
                                    f"Session perdue — {ok} produits sauvegardés en MariaDB"
                                )
                                if db_conn:
                                    db_conn.close()
                                return
                            log.error(f"Erreur produit {href}: {ex}")
                            err += 1

            except Exception as ex:
                if _session_perdue(ex):
                    log.error(f"Session perdue — {ok} produits sauvegardés en MariaDB")
                    if db_conn:
                        db_conn.close()
                    return
                log.error(f"Erreur catégorie {cat_url}: {ex}")

        log.info(f"Terminé — {ok} produits écrits, {err} erreurs")
        if db_conn:
            db_conn.close()

    else:  # mode search
        for item in products:
            try:
                driver.get(item["url"])
                rows = scraper.scrape_product()
                if rows and rows[0].get("isCombination") == "True":
                    parent_ref = rows[0].get("parentRef", "")
                    try:
                        grp_idx = resolve_decli_index("legallais", parent_ref)
                    except Exception:
                        grp_idx = 1
                    for _r in rows:
                        _r["combinationIndex"] = grp_idx
                if rows:
                    c1, c2, c3 = item.get("cat1", ""), item.get("cat2", ""), item.get("cat3", "")
                    for row in rows:
                        if db_conn:
                            try:
                                mapped = _map_to_csv_headers(row, c1, c2, c3)
                                is_combo = str(row.get("isCombination", "False")) == "True"
                                variant_ref = row.get("productRef", "") if is_combo else ""
                                mapped["product_fournisseur_url"] = _variant_url(item.get("url", ""), variant_ref)
                                insert_product(db_conn, "legallais", mapped)
                            except Exception as _db_exc:
                                log.debug(f"MariaDB produit ignoré : {_db_exc}")
                    log.info(f"  ✓ {rows[0].get('productRef','?')} — {len(rows)} ligne(s)")
                else:
                    log.warning(f"  ✗ Aucune donnée pour {item.get('url','')}")
            except Exception as ex:
                if _session_perdue(ex):
                    log.error("Session perdue — arrêt du mode search")
                    if db_conn:
                        db_conn.close()
                    return
                log.error(f"Erreur {item.get('url','')}: {ex}")
        if db_conn:
            db_conn.close()


# ─── POINT D'ENTRÉE ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scraper Legallais — parallèle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python scrap_legallais_products.py --mode browse                          # toutes catégories, 3 workers
  python scrap_legallais_products.py --mode browse --category "Bâtiment" --workers 4
  python scrap_legallais_products.py --mode search --file refs.json --workers 2
        """
    )
    parser.add_argument("--mode", choices=["browse", "search"], default="browse")
    parser.add_argument("--category", type=str, default=None)
    parser.add_argument("--file", type=str, default="refs.json")
    args = parser.parse_args()

    # ── Sélection interactive de la catégorie ──────────────────────────────────
    if args.mode == "browse" and args.category is None:
        print("\nCatégories disponibles :")
        for i, c in enumerate(CATEGORY_NAMES, 1):
            print(f"  {i:2d}. {c}")
        print(f"  {len(CATEGORY_NAMES) + 1:2d}. Toutes les catégories")
        while True:
            try:
                choice = int(input("\nChoisissez une catégorie (numéro) : ").strip())
                if 1 <= choice <= len(CATEGORY_NAMES):
                    args.category = CATEGORY_NAMES[choice - 1]
                    break
                elif choice == len(CATEGORY_NAMES) + 1:
                    break
            except (ValueError, EOFError):
                pass
            print(f"  → Entrez un numéro entre 1 et {len(CATEGORY_NAMES) + 1}")

    log.info(f"Mode: {args.mode} | Catégorie: {args.category or 'toutes'} | Destination: SQLite legallais.db")

    if args.mode == "browse":
        _scrape_direct({  # type: ignore[call-arg]
            "products": [],
            "mode": "browse",
            "category_filter": args.category,
        })
    else:
        log.info("=== Collecte des URLs produits (mode search) ===")
        products = _collect_search({"json_file": args.file})  # type: ignore[call-arg]
        if not products:
            log.error("Aucun produit collecté. Abandon.")
            return
        log.info(f"=== Scraping de {len(products)} produits ===")
        _scrape_direct({  # type: ignore[call-arg]
            "products": products,
            "mode": "search",
        })

    log.info("Scraping terminé — résultat en MariaDB")


def run_interactive():
    log.info("Démarrage scraping produits (browse) → MariaDB legallais.db")
    _scrape_direct({"products": [], "mode": "browse"})  # type: ignore[call-arg]


if __name__ == "__main__":
    main()


# ─── Wrapper GUI ───────────────────────────────────────────────────────────────

class LegallaisProductsScraper:
    """Wrapper synchrone exposant request_stop() pour la GUI (même pattern que LegallaisTrackingScraper)."""

    def __init__(self, category_filter=None):
        self._category_filter = category_filter

    def request_stop(self) -> None:
        global _stop_flag
        _stop_flag = True

    def run(self) -> None:
        global _stop_flag
        _stop_flag = False
        _scrape_direct({  # type: ignore[call-arg]
            "products": [],
            "mode": "browse",
            "category_filter": self._category_filter or None,
        })


def create_scraper(category_filter=None) -> LegallaisProductsScraper:
    """Factory attendue par la GUI."""
    return LegallaisProductsScraper(category_filter=category_filter)
