"""
Chef d'orchestre du scraper Prolians (mode : products) — point d'entrée officiel.

Ce fichier dirige l'ordre d'exécution en appelant les fonctions CSS de
scraper_prolians_products.py. Il contient main(), la boucle de scraping,
la gestion des sessions, la pagination sitemap et la persistance CSV.
"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import csv
import os
import argparse
import requests
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from auth.prolians.cookie_manager_prolians import ensure_logged_in, is_logged_in
from selectors.prolians import Selectors
from scrapers.Prolians_P3.products.scraper_prolians_products import (
    extract_sitemap_urls, extract_product_from_dom,
    FIELDNAMES, SITEMAP_INDEX
)

ROOT = PROJECT_ROOT
load_dotenv(ROOT / ".env")

USERNAME   = os.getenv("User_P3")
PASSWORD   = os.getenv("Password_P3")
LIMIT_TEST = None  # ex: 10 pour tester
crash_file = "log/crash_products.txt"


# =============================
# MAIN
# =============================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", type=int, default=1, help="Numéro de la partie (1-based)")
    parser.add_argument("--total", type=int, default=1, help="Nombre total de parties")
    args = parser.parse_args()

    if args.part < 1 or args.part > args.total:
        print(f" --part doit etre entre 1 et --total ({args.total})")
        sys.exit(1)

    run_ts = datetime.today().strftime("%Y-%m-%d_%H-%M")
    suffix = f"_part{args.part}of{args.total}" if args.total > 1 else ""
    csv_file   = str(ROOT / f"csv/scrap_p3_PW_{run_ts}{suffix}.csv")
    crash_file = f"log/crash_products{suffix}.txt"
    os.makedirs(str(ROOT / "csv"), exist_ok=True)
    os.makedirs("log", exist_ok=True)

    count_produit = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.set_default_timeout(8000)
        context.set_default_navigation_timeout(20000)
        page = context.new_page()

        # -------- LOGIN
        if not ensure_logged_in(page, context, USERNAME, PASSWORD):
            print("Impossible de se connecter — arrêt.")
            browser.close()
            sys.exit(1)

        # -------- REQUEST SESSION
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "fr-FR,fr;q=0.9",
        })
        for c in context.cookies():
            session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".prolians.fr"))

        # -------- SITEMAPS
        sitemap_files = extract_sitemap_urls(session, SITEMAP_INDEX)
        product_sitemap = next(u for u in sitemap_files if "product" in u)
        all_product_files = extract_sitemap_urls(session, product_sitemap)

        # Répartition par fichiers sitemap
        chunk = len(all_product_files) // args.total
        start = (args.part - 1) * chunk
        end = start + chunk if args.part < args.total else len(all_product_files)
        product_files = all_product_files[start:end]
        print(f" Partie {args.part}/{args.total} — {len(product_files)} fichiers sitemap ({start}—{end-1})")

        product_urls = []
        for f in product_files:
            product_urls.extend(extract_sitemap_urls(session, f))

        if LIMIT_TEST:
            product_urls = product_urls[:LIMIT_TEST]

        print(f" Produits détectés : {len(product_urls)}")

        # -------- CSV
        print(f"\n CSV : {csv_file}")
        file_exists = os.path.isfile(csv_file)

        with open(csv_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, FIELDNAMES, delimiter=";", quoting=csv.QUOTE_ALL)
            if not file_exists:
                writer.writeheader()

            try:
                for url in product_urls:
                    max_retries = 3
                    loaded = False
                    for attempt in range(max_retries):
                        try:
                            page.goto(url, wait_until="load", timeout=5000)
                            page.wait_for_selector(Selectors.title, timeout=5000)
                            loaded = True
                            break
                        except KeyboardInterrupt:
                            raise
                        except Exception:
                            print(f"Tentative {attempt+1}/{max_retries} échouée : {url}")

                    if not loaded:
                        print(f" Impossible de charger {url}")
                        with open(crash_file, "a", encoding="utf-8") as cf:
                            cf.write(url + "\n")
                        continue

                    if count_produit % 20 == 0 and not is_logged_in(page):
                        if not ensure_logged_in(page, context, USERNAME, PASSWORD):
                            print("Re-login échoué — arrêt.")
                            browser.close()
                            sys.exit(1)
                        page.goto(url, wait_until="domcontentloaded", timeout=10000)

                    rows = extract_product_from_dom(page)
                    if not rows:
                        print("Produit ignoré (data=None)")
                        with open(crash_file, "a", encoding="utf-8") as cf:
                            cf.write(url + "\n")
                        continue
                    for row in rows:
                        writer.writerow(row)
                    f.flush()
                    count_produit += 1
                    print(f"[{count_produit}] {rows[0]['productRef']} ({len(rows)} ligne(s))")
            except KeyboardInterrupt:
                print(f"\n Arrêt — {count_produit} produit(s) sauvegardé(s) dans:\n {csv_file}")

        browser.close()
        print(f"\n CSV généré : {csv_file}")


if __name__ == "__main__":
    main()


# =============================
# INTERFACE GUI
# =============================

import asyncio


class ProlianProductScraper:
    """Wrapper async du scraper Prolians products pour la GUI."""

    def __init__(self):
        load_dotenv(ROOT / ".env")
        self._user = os.getenv("User_P3")
        self._password = os.getenv("Password_P3")
        self._stop_requested = False

    async def run(self):
        await asyncio.to_thread(self._sync_run)

    def _sync_run(self):
        run_ts = datetime.today().strftime("%Y-%m-%d_%H-%M")
        csv_file = str(ROOT / f"csv/scrap_p3_PW_{run_ts}.csv")
        crash_f = str(ROOT / "log/crash_products.txt")
        os.makedirs(str(ROOT / "csv"), exist_ok=True)
        os.makedirs(str(ROOT / "log"), exist_ok=True)

        count = 0
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            context.set_default_timeout(8000)
            context.set_default_navigation_timeout(20000)
            page = context.new_page()

            if not ensure_logged_in(page, context, self._user, self._password):
                print("Impossible de se connecter — arrêt.")
                browser.close()
                return

            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept-Language": "fr-FR,fr;q=0.9",
            })
            for c in context.cookies():
                session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".prolians.fr"))

            sitemap_files = extract_sitemap_urls(session, SITEMAP_INDEX)
            product_sitemap = next(u for u in sitemap_files if "product" in u)
            all_product_files = extract_sitemap_urls(session, product_sitemap)
            product_urls = []
            for f in all_product_files:
                product_urls.extend(extract_sitemap_urls(session, f))

            if LIMIT_TEST:
                product_urls = product_urls[:LIMIT_TEST]
            print(f"Produits détectés : {len(product_urls)}")

            with open(csv_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, FIELDNAMES, delimiter=";", quoting=csv.QUOTE_ALL)
                writer.writeheader()

                for url in product_urls:
                    if self._stop_requested:
                        break
                    loaded = False
                    for attempt in range(3):
                        try:
                            page.goto(url, wait_until="load", timeout=5000)
                            page.wait_for_selector(Selectors.title, timeout=5000)
                            loaded = True
                            break
                        except Exception:
                            print(f"Tentative {attempt+1}/3 échouée : {url}")

                    if not loaded:
                        with open(crash_f, "a", encoding="utf-8") as cf:
                            cf.write(url + "\n")
                        continue

                    if count % 20 == 0 and not is_logged_in(page):
                        if not ensure_logged_in(page, context, self._user, self._password):
                            print("Re-login échoué — arrêt.")
                            break
                        page.goto(url, wait_until="domcontentloaded", timeout=10000)

                    rows = extract_product_from_dom(page)
                    if not rows:
                        with open(crash_f, "a", encoding="utf-8") as cf:
                            cf.write(url + "\n")
                        continue
                    for row in rows:
                        writer.writerow(row)
                    f.flush()
                    count += 1
                    print(f"[{count}] {rows[0]['productRef']}")

            browser.close()
            print(f"CSV généré : {csv_file}")

    def request_stop(self):
        self._stop_requested = True


def create_scraper() -> ProlianProductScraper:
    return ProlianProductScraper()
