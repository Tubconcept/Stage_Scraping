"""
Chef d'orchestre du scraper Prolians (mode : produits).

Parcourt les sitemaps XML du site pour lister les URLs produit, ouvre
chaque fiche dans Chromium (session authentifiée), délègue l'extraction
DOM à ``scraper_prolians_products`` et enregistre les lignes dans
``prolians.db``.

Fonctionnalités :
- Découpage par ``--part`` / ``--total`` pour paralléliser sur plusieurs machines ;
- Reprise sur crash via les URLs déjà présentes en base ;
- Reconnexion automatique si la session expire (~tous les 20 produits) ;
- Classe ``ProlianProductScraper`` pour l'intégration GUI (arrêt propre).
"""
import sys
from pathlib import Path

# --- Racine projet (auth, db, scrapers) ---
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
import csv
import os
import argparse
import requests
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from auth.prolians.cookie_manager import ensure_logged_in, is_logged_in
from css_selectors.prolians import Selectors
from scrapers.Prolians_P3.products.scraper_prolians_products import (
    extract_sitemap_urls, extract_product_from_dom,
    FIELDNAMES, SITEMAP_INDEX
)
from db.mariadb_db import init_site_db, insert_product, get_scraped_product_urls, resolve_decli_index
from core.logger import setup_logger

log = setup_logger("prolians.products")

ROOT = PROJECT_ROOT
load_dotenv(ROOT / ".env")

# Identifiants portail Prolians (fichier .env à la racine)
USERNAME   = os.getenv("User_P3")
PASSWORD   = os.getenv("Password_P3")
LIMIT_TEST = None  # ex: 10 pour limiter le nombre d'URLs en test
crash_file = "log/crash_products.txt"  # URLs en échec (surchargé si sharding)


# =============================
# MAIN
# =============================

def main():
    """Point d'entrée CLI avec sharding optionnel des fichiers sitemap."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", type=int, default=1, help="Numéro de la partie (1-based)")
    parser.add_argument("--total", type=int, default=1, help="Nombre total de parties")
    args = parser.parse_args()

    # Validation des paramètres de découpage
    if args.part < 1 or args.part > args.total:
        log.error(f"--part doit être entre 1 et --total ({args.total})")
        sys.exit(1)

    # Fichier de log d'échec distinct par shard pour faciliter la reprise manuelle
    suffix = f"_part{args.part}of{args.total}" if args.total > 1 else ""
    crash_file = f"log/crash_products{suffix}.txt"
    os.makedirs("log", exist_ok=True)

    # Base MariaDB site « prolians » (produits déjà scrappés)
    db_conn = None
    try:
        db_conn = init_site_db("prolians")
    except Exception as _exc:
        log.error(f"Base MariaDB Prolians non initialisée : {_exc}")

    count_produit = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.set_default_timeout(8000)
        context.set_default_navigation_timeout(20000)
        page = context.new_page()

        # -------- LOGIN
        if not ensure_logged_in(page, context, USERNAME, PASSWORD):
            log.error("Connexion Prolians échouée — arrêt.")
            browser.close()
            if db_conn:
                db_conn.close()
            sys.exit(1)

        # -------- SESSION HTTP (sitemaps XML, plus rapide que Playwright)
        # Réutilise les cookies Playwright pour accéder aux sitemaps authentifiés
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "fr-FR,fr;q=0.9",
        })
        for c in context.cookies():
            session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".prolians.fr"))

        # -------- SITEMAPS : index → sous-index produits → URLs articles
        sitemap_files = extract_sitemap_urls(session, SITEMAP_INDEX)
        product_sitemap = next(u for u in sitemap_files if "product" in u)
        all_product_files = extract_sitemap_urls(session, product_sitemap)

        # Découpage équitable des fichiers sitemap entre les workers (--part / --total)
        chunk = len(all_product_files) // args.total
        start = (args.part - 1) * chunk
        end = start + chunk if args.part < args.total else len(all_product_files)
        product_files = all_product_files[start:end]
        log.info(f"Partie {args.part}/{args.total} — {len(product_files)} fichiers sitemap ({start}—{end-1})")

        product_urls = []
        for f in product_files:
            product_urls.extend(extract_sitemap_urls(session, f))

        if LIMIT_TEST:
            product_urls = product_urls[:LIMIT_TEST]

        # Reprise : ignorer les URLs déjà présentes en base (évite doublons après crash)
        scraped_urls = get_scraped_product_urls(db_conn, "prolians") if db_conn else set()
        if scraped_urls:
            log.info(f"Reprise — {len(scraped_urls)} URL(s) déjà scrappée(s) ignorées")

        log.info(f"Produits détectés : {len(product_urls)}")

        try:
            for url in product_urls:
                if url in scraped_urls:
                    continue
                # Jusqu'à 3 tentatives de chargement (timeout court sur fiches lourdes)
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
                        log.warning(f"Tentative {attempt+1}/{max_retries} échouée : {url}")

                if not loaded:
                    log.warning(f"Impossible de charger {url}")
                    with open(crash_file, "a", encoding="utf-8") as cf:
                        cf.write(url + "\n")
                    continue

                # Contrôle session périodique : le portail déconnecte après inactivité
                if count_produit % 20 == 0 and not is_logged_in(page):
                    if not ensure_logged_in(page, context, USERNAME, PASSWORD):
                        log.error("Re-login échoué — arrêt.")
                        browser.close()
                        if db_conn:
                            db_conn.close()
                        sys.exit(1)
                    page.goto(url, wait_until="domcontentloaded", timeout=10000)

                rows = extract_product_from_dom(page)
                if not rows:
                    log.warning(f"Produit ignoré (data=None) : {url}")
                    with open(crash_file, "a", encoding="utf-8") as cf:
                        cf.write(url + "\n")
                    continue
                if any(r.get("products_is_combination") == "True" for r in rows):
                    parent_ref = rows[0].get("product_parent_reference", "")
                    try:
                        grp_idx = resolve_decli_index("prolians", parent_ref)
                    except Exception:
                        grp_idx = count_produit + 1
                    for row in rows:
                        if row.get("products_is_combination") == "True":
                            row["product_combination_index"] = grp_idx
                if db_conn:
                    for row in rows:
                        try:
                            db_row = dict(row)
                            db_row["product_fournisseur_url"] = url
                            insert_product(db_conn, "prolians", db_row)
                        except Exception:
                            pass
                scraped_urls.add(url)  # marque comme traité même si insert partiel
                count_produit += 1
                log.info(f"[{count_produit}] {rows[0]['product_reference_fournisseur']} ({len(rows)} ligne(s))")
        except KeyboardInterrupt:
            log.info(f"Arrêt — {count_produit} produit(s) enregistré(s) en MariaDB")

        browser.close()
        if db_conn:
            db_conn.close()
        log.info(f"{count_produit} produit(s) enregistré(s) en MariaDB")


if __name__ == "__main__":
    main()


# =============================
# INTERFACE GUI
# =============================

class ProlianProductScraper:
    """
    Wrapper GUI avec support d'arrêt — même pattern que ProlianTrackingScraper.

    ``run()`` est async pour s'intégrer à la boucle asyncio de l'interface ;
    le travail Playwright synchrone s'exécute dans un thread dédié.
    """

    def __init__(self):
        self._stop_requested = False

    def request_stop(self):
        """Signal d'arrêt demandé par l'utilisateur depuis la GUI."""
        self._stop_requested = True

    async def run(self):
        await asyncio.to_thread(self._sync_run)

    def _sync_run(self):
        """Version GUI : pas de sharding CLI, arrêt via ``request_stop``."""
        os.makedirs("log", exist_ok=True)

        # Connexion SQLite + URLs déjà scrappées pour reprise sans doublon
        db_conn = None
        try:
            db_conn = init_site_db("prolians")
        except Exception as _exc:
            log.error(f"Base MariaDB Prolians non initialisée : {_exc}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            context.set_default_timeout(8000)
            context.set_default_navigation_timeout(20000)
            page = context.new_page()

            if not ensure_logged_in(page, context, USERNAME, PASSWORD):
                log.error("Connexion Prolians échouée — arrêt.")
                browser.close()
                if db_conn:
                    db_conn.close()
                return

            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "fr-FR,fr;q=0.9",
            })
            for c in context.cookies():
                session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".prolians.fr"))

            sitemap_files   = extract_sitemap_urls(session, SITEMAP_INDEX)
            product_sitemap = next(u for u in sitemap_files if "product" in u)
            all_files       = extract_sitemap_urls(session, product_sitemap)
            product_urls: list = []
            for f in all_files:
                product_urls.extend(extract_sitemap_urls(session, f))

            # Reprise : ignorer les URLs déjà en base
            scraped_urls = get_scraped_product_urls(db_conn, "prolians") if db_conn else set()
            if scraped_urls:
                log.info(f"Reprise — {len(scraped_urls)} URL(s) déjà scrappée(s) ignorées")

            log.info(f"Produits détectés : {len(product_urls)}")
            count = 0

            for url in product_urls:
                if self._stop_requested:
                    log.info("Arrêt demandé.")
                    break
                if url in scraped_urls:
                    continue
                loaded = False
                for attempt in range(3):
                    try:
                        page.goto(url, wait_until="load", timeout=5000)
                        page.wait_for_selector(Selectors.title, timeout=5000)
                        loaded = True
                        break
                    except Exception:
                        log.warning(f"Tentative {attempt+1}/3 échouée : {url}")
                if not loaded:
                    with open(crash_file, "a", encoding="utf-8") as cf:
                        cf.write(url + "\n")
                    continue
                if count % 20 == 0 and not is_logged_in(page):
                    if not ensure_logged_in(page, context, USERNAME, PASSWORD):
                        break
                    page.goto(url, wait_until="domcontentloaded", timeout=10000)
                rows = extract_product_from_dom(page)
                if not rows:
                    with open(crash_file, "a", encoding="utf-8") as cf:
                        cf.write(url + "\n")
                    continue
                if any(r.get("products_is_combination") == "True" for r in rows):
                    parent_ref = rows[0].get("product_parent_reference", "")
                    try:
                        grp_idx = resolve_decli_index("prolians", parent_ref)
                    except Exception:
                        grp_idx = count + 1
                    for row in rows:
                        if row.get("products_is_combination") == "True":
                            row["product_combination_index"] = grp_idx
                if db_conn:
                    for row in rows:
                        try:
                            db_row = dict(row)
                            db_row["product_fournisseur_url"] = url
                            insert_product(db_conn, "prolians", db_row)
                        except Exception:
                            pass
                scraped_urls.add(url)
                count += 1
                log.info(f"[{count}] {rows[0]['product_reference_fournisseur']} ({len(rows)} ligne(s))")

            browser.close()
            if db_conn:
                db_conn.close()
            log.info(f"{count} produit(s) enregistré(s) en MariaDB")


def create_scraper() -> ProlianProductScraper:
    """Fabrique attendue par ``gui/site_config`` pour instancier le scraper."""
    return ProlianProductScraper()
