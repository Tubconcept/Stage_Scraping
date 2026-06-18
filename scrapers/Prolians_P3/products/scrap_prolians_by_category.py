"""
Scraper Prolians (mode : produits par catégorie).

Au lieu d'énumérer les produits via les sitemaps XML, ce module **navigue dans
l'arbre des catégories** du site en suivant les URLs propres `/nos-produits/...`.

Pourquoi pas le méga-menu « Produits » ? Ses boutons sont des composants React
sans `href`, avec des IDs dynamiques (react-aria) et un overlay qui intercepte
les clics — donc impilotable de façon fiable. En revanche, chaque catégorie a
une URL stable et hiérarchique (visible dans le fil d'Ariane), ex. :

    /nos-produits                                   (racine)
    /nos-produits/outillage                         (niveau 1)
    /nos-produits/outillage/outillage-a-main        (niveau 2)
    /nos-produits/outillage/.../compositions-...    (feuille : produits)

Algorithme :
  1. Point(s) de départ : les 10 catégories principales (ou une seule si filtrée).
  2. Parcours en largeur (BFS) de toutes les sous-catégories sous le préfixe.
  3. Sur chaque page : collecte des URLs produit, pagination via `?p=N`.
  4. Pour chaque produit : extraction DOM (réutilise scraper_prolians_products)
     puis insertion MariaDB — même logique/colonnes que le mode sitemap.

Le fil d'Ariane de chaque fiche renseigne déjà ``product_category_tree``.

Factory attendue par la GUI : ``create_scraper(category_name="") -> ProlianByCategoryScraper``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# --- Racine projet (auth, db, scrapers) ---
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import asyncio
import os
import re
from collections import deque
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from auth.prolians.cookie_manager import ensure_logged_in, is_logged_in
from css_selectors.prolians import Selectors
from db.mariadb_db import (
    init_site_db,
    insert_product,
    get_scraped_product_urls,
    resolve_decli_index,
)
from scrapers.Prolians_P3.products.scraper_prolians_products import extract_product_from_dom
from core.logger import setup_logger

log = setup_logger("prolians.products")

load_dotenv(PROJECT_ROOT / ".env")
USERNAME = os.getenv("User_P3")
PASSWORD = os.getenv("Password_P3")

BASE_URL = Selectors.BASE_URL


# =============================
# HELPERS NAVIGATION CATÉGORIE
# =============================

def _norm_path(href: str) -> str:
    """Normalise un href en chemin absolu sans query ni slash final.

    '/nos-produits/outillage/?p=2' -> '/nos-produits/outillage'
    """
    if not href:
        return ""
    parts = urlsplit(href)
    path = parts.path.rstrip("/")
    return path


def _to_full_url(path_or_href: str) -> str:
    """Construit une URL absolue à partir d'un href relatif ou absolu."""
    if path_or_href.startswith("http"):
        return path_or_href
    return f"{BASE_URL}{path_or_href}"


def _collect_subcategory_paths(page) -> list[str]:
    """Retourne les chemins de (sous-)catégories liés sur la page courante."""
    try:
        hrefs = page.eval_on_selector_all(
            Selectors.category_link,
            "els => Array.from(new Set(els.map(e => e.getAttribute('href')).filter(Boolean)))",
        )
    except Exception:
        return []
    out = []
    seen = set()
    for h in hrefs:
        p = _norm_path(h)
        # Conserve uniquement les vraies sous-catégories (au moins un niveau sous /nos-produits)
        if p.startswith("/nos-produits/") and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _collect_product_hrefs(page) -> list[str]:
    """Retourne les hrefs produit (slug simple) présents dans les cartes."""
    try:
        return page.eval_on_selector_all(
            Selectors.product_card_link,
            """els => Array.from(new Set(els.map(e => e.getAttribute('href'))
                .filter(h => h && h.startsWith('/')
                    && (h.match(/\\//g) || []).length === 1
                    && h.length > 8
                    && !h.startsWith('/nos-produits')
                    && !h.startsWith('/brand')
                    && !h.startsWith('/account')
                    && !h.startsWith('/checkout')
                    && !h.startsWith('/login'))))""",
        )
    except Exception:
        return []


def _detect_total_pages(page) -> int:
    """Déduit le nombre total de pages d'une catégorie (pagination ?p=N).

    Combine les liens de pagination (?p=2, ?p=3…) et le texte « X sur Y ».
    """
    total = 1
    try:
        hrefs = page.eval_on_selector_all(
            Selectors.pagination_link,
            "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
        )
        for h in hrefs:
            m = re.search(r"[?&]p=(\d+)", h)
            if m:
                total = max(total, int(m.group(1)))
    except Exception:
        pass
    try:
        body = page.locator("body").inner_text(timeout=3000)
        m = re.search(r"\b\d+\s+sur\s+(\d+)\b", body, re.IGNORECASE)
        if m:
            total = max(total, int(m.group(1)))
    except Exception:
        pass
    return total


def _page_url(cat_url: str, page_num: int) -> str:
    """URL d'une page de listing (page 1 = URL nue, sinon ?p=N)."""
    if page_num <= 1:
        return cat_url
    sep = "&" if "?" in cat_url else "?"
    return f"{cat_url}{sep}p={page_num}"


# =============================
# SCRAPER
# =============================

class ProlianByCategoryScraper:
    """Parcourt l'arbre des catégories Prolians et scrappe les fiches produit.

    ``run()`` est async (asyncio.to_thread) pour s'intégrer à la boucle asyncio
    de la GUI ; le travail Playwright synchrone tourne dans un thread dédié —
    même pattern que ``ProlianProductScraper``.
    """

    def __init__(self, category_name: str = "", limit: int | None = None) -> None:
        name = (category_name or "").strip()
        # « Toutes les catégories » (entrée GUI) = crawl complet
        if name == Selectors.ALL_CATEGORIES_LABEL:
            name = ""
        self._category_name = name
        self._limit = limit
        self._stop_requested = False

    def request_stop(self) -> None:
        """Signal d'arrêt demandé par l'utilisateur depuis la GUI."""
        self._stop_requested = True

    async def run(self) -> None:
        await asyncio.to_thread(self._sync_run)

    # ─── Points de départ du parcours ─────────────────────────────────────────

    def _seed_categories(self, page) -> tuple[list[str], str | None]:
        """Retourne (chemins de départ, préfixe de confinement).

        - Catégorie sélectionnée → départ sur cette seule branche (confinée).
        - Sinon → les 10 catégories principales découvertes sur /nos-produits
          (fallback sur la table statique CATEGORY_URLS).
        """
        if self._category_name:
            slug = Selectors.CATEGORY_URLS.get(self._category_name)
            if not slug:
                log.error("Catégorie inconnue : %r", self._category_name)
                return [], None
            root = f"/nos-produits/{slug}"
            return [root], root

        # Découverte dynamique des catégories principales
        seeds: list[str] = []
        try:
            page.goto(Selectors.PRODUCTS_ROOT_URL, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(2500)
            seeds = _collect_subcategory_paths(page)
        except Exception as exc:
            log.warning("Découverte /nos-produits échouée (%s) — table statique utilisée", exc)
        if not seeds:
            seeds = [f"/nos-produits/{slug}" for slug in Selectors.CATEGORY_URLS.values()]
        return seeds, None

    # ─── Parcours BFS des catégories → URLs produit ───────────────────────────

    def _crawl_product_urls(self, page, seeds: list[str], confine: str | None) -> list[str]:
        """BFS sur l'arbre catégories. Retourne les URLs produit dédupliquées."""
        visited_cats: set[str] = set()
        queue: deque[str] = deque(dict.fromkeys(seeds))  # dédup en gardant l'ordre
        product_paths: list[str] = []
        seen_products: set[str] = set()

        while queue:
            if self._stop_requested:
                break
            cat_path = queue.popleft()
            if cat_path in visited_cats:
                continue
            visited_cats.add(cat_path)
            cat_url = _to_full_url(cat_path)

            try:
                page.goto(cat_url, wait_until="domcontentloaded", timeout=25000)
            except Exception:
                log.warning("Catégorie injoignable : %s", cat_url)
                continue
            # Laisse le listing React se rendre ; sinon ce n'est pas une feuille produit
            try:
                page.wait_for_selector(Selectors.product_card, timeout=4000)
            except Exception:
                pass

            # Enfile les sous-catégories (confinées au préfixe si filtre actif)
            for sub in _collect_subcategory_paths(page):
                if sub in visited_cats:
                    continue
                if confine and not (sub == confine or sub.startswith(confine + "/")):
                    continue
                queue.append(sub)

            # Collecte les produits sur toutes les pages de cette catégorie
            total_pages = _detect_total_pages(page)
            for pnum in range(1, total_pages + 1):
                if self._stop_requested:
                    break
                if pnum > 1:
                    try:
                        page.goto(_page_url(cat_url, pnum), wait_until="domcontentloaded", timeout=25000)
                        page.wait_for_selector(Selectors.product_card, timeout=4000)
                    except Exception:
                        continue
                new_here = 0
                for href in _collect_product_hrefs(page):
                    p = _norm_path(href)
                    if p and p not in seen_products:
                        seen_products.add(p)
                        product_paths.append(p)
                        new_here += 1
                if new_here:
                    log.info("  %s (p%d/%d) — +%d produit(s) [total %d]",
                             cat_path, pnum, total_pages, new_here, len(product_paths))

        log.info("Parcours terminé : %d catégorie(s) visitée(s), %d produit(s) uniques",
                 len(visited_cats), len(product_paths))
        return [_to_full_url(p) for p in product_paths]

    # ─── Extraction + persistance d'un produit (logique alignée sur le mode sitemap) ─

    def _scrape_product(self, page, context, db_conn, url: str, count: int) -> bool:
        """Charge la fiche, extrait et insère les lignes. True si enregistrée."""
        loaded = False
        for attempt in range(3):
            try:
                page.goto(url, wait_until="load", timeout=5000)
                page.wait_for_selector(Selectors.title, timeout=5000)
                loaded = True
                break
            except Exception:
                log.warning(f"Tentative {attempt + 1}/3 échouée : {url}")
        if not loaded:
            return False

        # Contrôle session périodique (le portail déconnecte après inactivité)
        if count % 20 == 0 and not is_logged_in(page):
            if not ensure_logged_in(page, context, USERNAME, PASSWORD):
                raise RuntimeError("Re-login échoué")
            page.goto(url, wait_until="domcontentloaded", timeout=10000)

        rows = extract_product_from_dom(page)
        if not rows:
            log.warning(f"Produit ignoré (data=None) : {url}")
            return False

        # Index de groupe pour les déclinaisons (combinations)
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
        log.info(f"[{count + 1}] {rows[0]['product_reference_fournisseur']} ({len(rows)} ligne(s))")
        return True

    # ─── Boucle principale ────────────────────────────────────────────────────

    def _sync_run(self) -> None:
        os.makedirs("log", exist_ok=True)
        crash_file = "log/crash_products_category.txt"

        db_conn = None
        try:
            db_conn = init_site_db("prolians")
        except Exception as exc:
            log.error(f"Base MariaDB Prolians non initialisée : {exc}")

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

            seeds, confine = self._seed_categories(page)
            if not seeds:
                log.error("Aucune catégorie de départ — arrêt.")
                browser.close()
                if db_conn:
                    db_conn.close()
                return

            if confine:
                log.info("Périmètre : « %s » — confiné à %s", self._category_name, confine)
            else:
                log.warning("Périmètre : CATALOGUE COMPLET — %d catégorie(s) principale(s)", len(seeds))

            product_urls = self._crawl_product_urls(page, seeds, confine)
            if self._limit:
                product_urls = product_urls[: self._limit]

            # Reprise : ignorer les URLs déjà en base
            scraped_urls = get_scraped_product_urls(db_conn, "prolians") if db_conn else set()
            if scraped_urls:
                log.info(f"Reprise — {len(scraped_urls)} URL(s) déjà scrappée(s) ignorées")

            count = 0
            try:
                for url in product_urls:
                    if self._stop_requested:
                        log.info("Arrêt demandé.")
                        break
                    if url in scraped_urls:
                        continue
                    try:
                        ok = self._scrape_product(page, context, db_conn, url, count)
                    except RuntimeError:
                        log.error("Session perdue — arrêt.")
                        break
                    if not ok:
                        with open(crash_file, "a", encoding="utf-8") as cf:
                            cf.write(url + "\n")
                        continue
                    scraped_urls.add(url)
                    count += 1
            except KeyboardInterrupt:
                log.info(f"Arrêt clavier — {count} produit(s) enregistré(s)")

            browser.close()
            if db_conn:
                db_conn.close()
            log.info(f"{count} produit(s) enregistré(s) en MariaDB")


def create_scraper(category_name: str = "") -> ProlianByCategoryScraper:
    """Fabrique attendue par ``gui/site_config`` pour instancier le scraper."""
    return ProlianByCategoryScraper(category_name=category_name)


# =============================
# CLI
# =============================

def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper Prolians par catégorie")
    parser.add_argument("--category", default="", help="Nom exact d'une catégorie principale (vide = toutes)")
    parser.add_argument("--limit", type=int, default=None, help="Limiter le nombre de produits (test)")
    args = parser.parse_args()
    ProlianByCategoryScraper(category_name=args.category, limit=args.limit)._sync_run()


if __name__ == "__main__":
    main()
