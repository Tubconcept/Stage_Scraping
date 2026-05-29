"""
Script principal de scraping Legallais - Version parallèle

Architecture en 2 phases :
  Phase 1 : 1 navigateur collecte toutes les URLs produits avec leurs catégories
  Phase 2 : N navigateurs parallèles se partagent les produits en lots
             chaque worker écrit dans son propre CSV temporaire
  Fusion   : les CSV temporaires sont fusionnés en un fichier final

Usage:
    python scrap_p1_bot.py --mode browse [--category "nom_categorie"] [--workers 3]
    python scrap_p1_bot.py --mode search --file refs.json [--workers 3]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import json
import csv
import argparse
from typing import List, Dict, Optional
from botasaurus.browser import browser, Driver
from core.config import BASE_URL, JSON_DIR, CSV_DIR, SELECTORS
from core.utils import logger, get_timestamped_filename
from core.scraper_bot import LegallaisScraper
from core.csv_manager import CSVManager

# Borne haute du décorateur @browser — le nombre effectif de workers
# est contrôlé par le nombre de lots passés en argument.
_MAX_PARALLEL = 8


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


def _merge_csv(temp_names: List[str], output_filename: str) -> int:
    """
    Fusionne les CSV temporaires en un fichier final.
    Supprime les fichiers temporaires. Retourne le nombre de lignes produit.
    """
    output_path = CSV_DIR / output_filename
    rows_written = 0
    first = True

    with open(output_path, mode='w', newline='', encoding='utf-8') as out_f:
        writer = csv.writer(out_f, delimiter=';')
        for name in temp_names:
            path = CSV_DIR / name
            if not path.exists():
                continue
            with open(path, mode='r', newline='', encoding='utf-8') as in_f:
                reader = csv.reader(in_f, delimiter=';')
                for i, row in enumerate(reader):
                    if i == 0 and not first:
                        continue  # sauter l'en-tête des fichiers suivants
                    writer.writerow(row)
                    if i > 0:
                        rows_written += 1
            first = False
            path.unlink()

    return rows_written


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
        logger.warning("Aucune catégorie trouvée")
        return []

    # Exclure les URLs de type "guides-de-choix" qui bloquent le scraper
    categories = [c for c in categories if "guides-de-choix" not in c]
    logger.info(f"Collecte de {len(categories)} catégories")
    all_products: List[Dict] = []

    for idx, category_url in enumerate(categories, 1):
        logger.info(f"[{idx}/{len(categories)}] Catégorie: {category_url}")
        try:
            driver.get(_to_full_url(category_url))
            scraper.set_items_per_page()  # 100 articles/page (ITEMS_PER_PAGE)

            cat1, cat2, cat3 = scraper.get_breadcrumb_categories()
            total_pages = scraper.get_page_count()
            logger.info(f"  {total_pages} page(s) dans cette catégorie")

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

            logger.info(f"  Total cumulé: {len(all_products)} produits")
        except Exception as e:
            logger.error(f"Erreur collecte catégorie {category_url}: {e}")

    logger.info(f"Phase 1 terminée — {len(all_products)} produits collectés")
    return all_products


@browser(headless=False)
def _collect_search(driver: Driver, data: dict = None) -> List[Dict]:  # type: ignore[assignment]
    """
    Phase 1 (mode search) : recherche chaque référence et collecte l'URL produit.
    Les catégories ne sont PAS récupérées ici (les workers le feront lors du
    scraping, évitant une double visite de chaque page produit).
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
        logger.info(f"[{idx}/{len(product_refs)}] Recherche: {ref}")
        try:
            driver.wait_for_element(SELECTORS["search_input"], 1)
            driver.clear(SELECTORS["search_input"], 1)
            driver.type(SELECTORS["search_input"], str(ref).zfill(6), 1)

            try:
                driver.wait_for_element(SELECTORS["search_result"], 1)
            except Exception:
                logger.error(f"Aucun résultat pour la ref: {ref}")
                continue

            if not driver.is_element_present(SELECTORS["search_result"]):
                logger.warning(f"Résultat absent pour: {ref}")
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
                # cat1/cat2/cat3 vides : les workers les liront depuis le breadcrumb
                all_products.append({"url": product_url, "cat1": "", "cat2": "", "cat3": ""})
                logger.info(f"  URL collectée: {product_url}")
            else:
                logger.warning(f"Référence exacte non trouvée: {ref}")

        except Exception as e:
            logger.error(f"Erreur recherche {ref}: {e}")

    logger.info(f"Phase 1 terminée — {len(all_products)} produits collectés")
    return all_products


# ─── PHASE 2 : scraping parallèle ─────────────────────────────────────────────

@browser(headless=True, parallel=_MAX_PARALLEL)
def _scrape_batch(driver: Driver, data: dict) -> None:
    """
    Worker parallèle : reçoit un lot de produits {"url", "cat1", "cat2", "cat3"}.
    - Si cat1 est vide (mode search), lit les catégories depuis le breadcrumb.
    - Écrit dans un CSV temporaire propre à ce worker.
    Le nombre de workers actifs = nombre de lots passés à l'appel.
    """
    batch_id: int = data["batch_id"]
    products: List[Dict] = data["products"]
    temp_csv: str = data["temp_csv"]

    scraper = LegallaisScraper()
    scraper.set_driver(driver)
    scraper.connexion()
    csv_manager = CSVManager(temp_csv)

    logger.info(f"[Worker {batch_id}] Démarrage — {len(products)} produits à traiter")
    ok, err = 0, 0

    for item in products:
        try:
            driver.get(item["url"])

            # Mode search : catégories non pré-remplies → les lire depuis le breadcrumb
            cat1, cat2, cat3 = item["cat1"], item["cat2"], item["cat3"]

            rows = scraper.scrape_product()
            if rows:
                for row in rows:
                    csv_manager.write_row(row, cat1, cat2, cat3)
                ok += 1
                logger.info(f"[Worker {batch_id}] ✓ {rows[0].get('productRef','?')} — {len(rows)} ligne(s)")
            else:
                logger.warning(f"[Worker {batch_id}] ✗ Aucune donnée pour {item['url']}")
                err += 1
        except Exception as e:
            logger.error(f"[Worker {batch_id}] Erreur {item['url']}: {e}")
            err += 1

    logger.info(f"[Worker {batch_id}] Terminé — {ok} OK, {err} erreurs")


# ─── POINT D'ENTRÉE ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scraper Legallais — parallèle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python scrap_p1_bot.py --mode browse                          # toutes catégories, 3 workers
  python scrap_p1_bot.py --mode browse --category "Bâtiment" --workers 4
  python scrap_p1_bot.py --mode search --file refs.json --workers 2
        """
    )
    parser.add_argument("--mode", choices=["browse", "search"], default="browse",
                        help="Mode de scraping")
    parser.add_argument("--category", type=str, default=None,
                        help="Filtrer par catégorie niveau 1 (mode browse)")
    parser.add_argument("--file", type=str, default="refs.json",
                        help="Fichier JSON des références (mode search)")
    parser.add_argument("--output", type=str, default=None,
                        help="Nom du fichier CSV de sortie")
    parser.add_argument("--workers", type=int, default=3,
                        help=f"Navigateurs parallèles pour la phase 2 (1-{_MAX_PARALLEL}, défaut: 3)")
    args = parser.parse_args()

    if not (1 <= args.workers <= _MAX_PARALLEL):
        parser.error(f"--workers doit être entre 1 et {_MAX_PARALLEL}")

    basename = (
        f"scrap_p1_PW_{args.category or 'all'}"
        if args.mode == "browse"
        else "scrap_p1_search"
    )
    csv_filename = args.output or get_timestamped_filename(basename)

    logger.info(f"Mode: {args.mode} | Workers: {args.workers} | Sortie: {csv_filename}")

    # ── Phase 1 : collecte des URLs (navigateur unique) ──
    logger.info("=== Phase 1 : collecte des URLs produits ===")
    if args.mode == "browse":
        products = _collect_browse({"category_filter": args.category})  # type: ignore[call-arg]
    else:
        products = _collect_search({"json_file": args.file})  # type: ignore[call-arg]

    if not products:
        logger.error("Aucun produit collecté. Abandon.")
        return

    logger.info(f"=== Phase 2 : scraping de {len(products)} produits avec {args.workers} workers ===")

    # ── Phase 2 : répartition en lots + scraping parallèle ──
    batches = _split_batches(products, args.workers)
    temp_names = [f"_temp_batch_{i}.csv" for i in range(len(batches))]
    batch_data = [
        {"batch_id": i, "products": b, "temp_csv": temp_names[i]}
        for i, b in enumerate(batches)
    ]
    _scrape_batch(batch_data)  # type: ignore[call-arg]

    # ── Phase 3 : fusion des CSV temporaires ──
    logger.info("=== Phase 3 : fusion des CSV ===")
    total = _merge_csv(temp_names, csv_filename)
    logger.info(f"Scraping terminé — {total} produits écrits dans {csv_filename}")


def _session_perdue(ex: Exception) -> bool:
    """Retourne True si l'exception indique une perte de session Chrome."""
    msg = str(ex).lower()
    return not msg or "target" in msg or "goodbye" in msg or "connection" in msg or "closed" in msg


@browser(headless=False)
def _scrape_direct(driver: Driver, data: dict = None) -> None:
    """Scrape séquentiel : 1 navigateur, écriture immédiate dans le CSV."""
    products: List[Dict] = data.get("products", [])
    csv_filename: str = data.get("csv_filename", "scrap_p1_Produits.csv")
    mode: str = data.get("mode", "browse")

    scraper = LegallaisScraper()
    scraper.set_driver(driver)
    scraper.connexion()
    csv_manager = CSVManager(csv_filename)

    if mode == "browse":
        driver.get(BASE_URL)
        driver.wait_for_element(".o-menu__items__list")
        driver.move_mouse_to_element("#products-navbar-button")
        driver.click("#products-navbar-button")
        categories = scraper.get_categories()
        categories = [c for c in categories if "guides-de-choix" not in c]
        logger.info(f"{len(categories)} catégories trouvées")

        ok, err = 0, 0
        visited_urls: set = set()

        for idx, cat_url in enumerate(categories, 1):
            try:
                full_url = cat_url if cat_url.startswith("http") else BASE_URL + cat_url
                driver.get(full_url)

                scraper.set_items_per_page()
                # Attendre le breadcrumb : rendu par JS, doit être dans le DOM
                # avant d'appeler get_breadcrumb_categories() pour avoir cat3
                try:
                    driver.wait_for_element(SELECTORS["breadcrumb"], 5)
                except Exception:
                    pass
                cat1, cat2, cat3 = scraper.get_breadcrumb_categories()
                logger.info(f"  Breadcrumb : {cat1} > {cat2} > {cat3}")
                # Attendre que la liste produits soit rechargée (100 items)
                try:
                    driver.wait_for_element(SELECTORS["product_card"], 3)
                except Exception:
                    pass

                total_pages = scraper.get_page_count()
                logger.info(
                    f"[{idx}/{len(categories)}] {cat1} > {cat2} > {cat3}"
                    f" — {total_pages} page(s)"
                )

                for page_num in range(1, total_pages + 1):
                    if page_num > 1:
                        scraper.go_to_page(page_num)

                    for href in scraper.get_product_links():
                        try:
                            full_href = href if href.startswith("http") else BASE_URL + href
                            if full_href in visited_urls:
                                continue
                            visited_urls.add(full_href)

                            logger.info(f"  → Produit {ok + err + 1} : {full_href}")
                            driver.get(full_href)
                            rows = scraper.scrape_product()

                            if rows:
                                for row in rows:
                                    csv_manager.write_row(row, cat1, cat2, cat3)
                                ok += 1
                                ref = rows[0].get("productRef", "?")
                                logger.info(f"  ✓ [{ok}] {ref} — {len(rows)} ligne(s) écrite(s)")
                            else:
                                err += 1
                                logger.warning(f"  ✗ Aucune donnée : {full_href}")

                        except Exception as ex:
                            if _session_perdue(ex):
                                logger.error(
                                    f"Session perdue — {ok} produits sauvegardés dans {csv_filename}"
                                )
                                return
                            logger.error(f"Erreur produit {href}: {ex}")
                            err += 1

            except Exception as ex:
                if _session_perdue(ex):
                    logger.error(f"Session perdue — {ok} produits sauvegardés dans {csv_filename}")
                    return
                logger.error(f"Erreur catégorie {cat_url}: {ex}")

        logger.info(f"Terminé — {ok} produits écrits, {err} erreurs")

    else:  # mode search — les cats viennent du breadcrumb de la page produit via le JS
        for item in products:
            try:
                driver.get(item["url"])
                rows = scraper.scrape_product()
                if rows:
                    for row in rows:
                        csv_manager.write_row(
                            row,
                            item.get("cat1", ""),
                            item.get("cat2", ""),
                            item.get("cat3", ""),
                        )
                    logger.info(f"  ✓ {rows[0].get('productRef','?')} — {len(rows)} ligne(s)")
                else:
                    logger.warning(f"  ✗ Aucune donnée pour {item.get('url','')}")
            except Exception as ex:
                if _session_perdue(ex):
                    logger.error("Session perdue — arrêt du mode search")
                    return
                logger.error(f"Erreur {item.get('url','')}: {ex}")


def run_interactive():
    csv_filename = "scrap_p1_Produits.csv"
    logger.info(f"Démarrage scraping produits (browse) | Fichier: {csv_filename}")
    _scrape_direct({"products": [], "csv_filename": csv_filename, "mode": "browse"})  # type: ignore[call-arg]


if __name__ == "__main__":
    main()
