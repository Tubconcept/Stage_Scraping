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

# Garde-fou anti-boucle : nb max de pages de listing parcourues par catégorie.
_MAX_PAGES_PER_CATEGORY = 500


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


def _collect_product_refs(page) -> list[str]:
    """Retourne les références (SKU) lues sur les cartes produit de la page.

    Utilisé par le mode catalogue GraphQL : la référence article (8 chiffres)
    est plus fiable que le suffixe du slug d'URL (qui n'est pas toujours la réf).
    """
    try:
        texts = page.eval_on_selector_all(
            Selectors.search_result_ref,  # span[data-testid="product-card/reference"]
            "els => els.map(e => (e.textContent || '').trim())",
        )
    except Exception:
        return []
    refs = []
    for t in texts:
        m = re.search(r"\d{6,}", t or "")
        if m:
            refs.append(m.group(0))
    return refs


def _has_next_page(page, current_page: int) -> bool:
    """Indique s'il existe une page de listing au-delà de ``current_page``.

    Prolians pagine via un bouton React « Page suivante » (sans ``<a href>``),
    désactivé sur la dernière page : on ne peut donc pas connaître le total a
    priori. On teste ce bouton en priorité (présent + non désactivé), avec un
    repli sur un éventuel lien ``?p=N`` strictement supérieur à la page courante.
    """
    # 1) Bouton « Page suivante » (cas nominal)
    try:
        btn = page.locator(Selectors.pagination_next).first
        if btn.count() > 0:
            disabled = btn.is_disabled()
            aria = (btn.get_attribute("aria-disabled") or "").lower()
            return not (disabled or aria == "true")
    except Exception:
        pass
    # 2) Repli : un lien ?p=N supérieur à la page courante existe-t-il ?
    try:
        hrefs = page.eval_on_selector_all(
            Selectors.pagination_link,
            "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
        )
        for h in hrefs:
            m = re.search(r"[?&]p=(\d+)", h or "")
            if m and int(m.group(1)) > current_page:
                return True
    except Exception:
        pass
    return False


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

    # ─── Parcours BFS des catégories (générique) ──────────────────────────────

    def _open_category(self, page, url: str) -> bool:
        """Charge une page de listing et laisse le rendu React se faire.

        Retourne False si la page est injoignable.
        """
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception:
            log.warning("Catégorie injoignable : %s", url)
            return False
        # Laisse le listing React se rendre ; sinon ce n'est pas une feuille produit.
        try:
            page.wait_for_selector(Selectors.product_card, timeout=8000)
            page.wait_for_timeout(2000)  # React injecte les cartes après le 1er selector
        except Exception:
            pass
        return True

    def _enqueue_subcategories(self, page, queue: deque, visited_cats: set,
                               confine: str | None) -> None:
        """Enfile les sous-catégories liées (confinées au préfixe si filtre actif)."""
        for sub in _collect_subcategory_paths(page):
            if sub in visited_cats:
                continue
            if confine and not (sub == confine or sub.startswith(confine + "/")):
                continue
            queue.append(sub)

    def _paginate_category(self, page, cat_path: str, cat_url: str, collect_fn,
                           seen: set, items: list, label: str) -> None:
        """Collecte les items de toutes les pages d'une catégorie (mute seen/items).

        Pagination pilotée par le bouton « Page suivante » (cf. ``_has_next_page``) :
        on avance page par page tant qu'il existe une suite, avec garde-fou
        anti-boucle et arrêt dès qu'une page ne contient plus aucun produit.
        """
        pnum = 1
        while pnum <= _MAX_PAGES_PER_CATEGORY:
            if self._stop_requested:
                return
            # Page 1 déjà chargée par l'appelant ; les suivantes via ?p=N.
            if pnum > 1 and not self._open_category(page, _page_url(cat_url, pnum)):
                return
            page_items = collect_fn(page)
            new_here = 0
            for it in page_items:
                if it and it not in seen:
                    seen.add(it)
                    items.append(it)
                    new_here += 1
            if new_here:
                log.info("  %s (p%d) — +%d %s [total %d]",
                         cat_path, pnum, new_here, label, len(items))
            if not page_items or not _has_next_page(page, pnum):
                return
            pnum += 1

    def _crawl_categories(self, page, seeds: list[str], confine: str | None,
                          collect_fn, label: str = "produit") -> list:
        """BFS sur l'arbre catégories, paginant chaque catégorie.

        ``collect_fn(page)`` extrait les items d'une page de listing (URLs ou
        références). Retourne la liste dédupliquée dans l'ordre de découverte.
        Le confinement (``confine``) restreint le parcours à une branche.
        """
        visited_cats: set[str] = set()
        queue: deque[str] = deque(dict.fromkeys(seeds))  # dédup en gardant l'ordre
        items: list = []
        seen: set = set()

        while queue and not self._stop_requested:
            cat_path = queue.popleft()
            if cat_path in visited_cats:
                continue
            visited_cats.add(cat_path)
            cat_url = _to_full_url(cat_path)
            if not self._open_category(page, cat_url):
                continue
            self._enqueue_subcategories(page, queue, visited_cats, confine)
            self._paginate_category(page, cat_path, cat_url, collect_fn, seen, items, label)

        log.info("Parcours terminé : %d catégorie(s) visitée(s), %d %s unique(s)",
                 len(visited_cats), len(items), label)
        return items

    def _crawl_product_urls(self, page, seeds: list[str], confine: str | None) -> list[str]:
        """BFS → URLs produit absolues, dédupliquées."""
        paths = self._crawl_categories(
            page, seeds, confine,
            lambda pg: [_norm_path(h) for h in _collect_product_hrefs(pg)],
            label="produit",
        )
        return [_to_full_url(p) for p in paths]

    def _crawl_product_refs(self, page, seeds: list[str], confine: str | None) -> list[str]:
        """BFS → références produit (SKU), dédupliquées. Pour le mode GraphQL."""
        return self._crawl_categories(page, seeds, confine, _collect_product_refs, label="réf")

    # ─── Extraction + persistance d'un produit (logique alignée sur le mode sitemap) ─

    def _scrape_product(self, page, context, db_conn, url: str, count: int) -> bool:
        """Charge la fiche, extrait et insère les lignes. True si enregistrée.

        Chargement robuste : ``domcontentloaded`` + attente explicite du titre
        (l'événement ``load`` des pages React Prolians dépasse souvent 5 s).

        Garde-fou de session : une fiche se charge **même déconnecté** (Prolians
        n'y redirige pas vers le login — il affiche le *prix public* au lieu du
        prix négocié). On vérifie donc ``is_logged_in`` après chaque chargement et
        on se reconnecte avant d'extraire si besoin ; sinon une session qui expire
        en cours de run remplirait la base de prix publics erronés, en silence.
        """
        def _load_once() -> bool:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_selector(Selectors.title, timeout=8000)
                return True
            except Exception:
                return False

        loaded = False
        for attempt in range(3):
            if _load_once() and is_logged_in(page):
                loaded = True
                break
            # Fiche non chargée, ou chargée hors session : reconnexion si déconnecté.
            if not is_logged_in(page):
                log.info("Session perdue sur la fiche — reconnexion…")
                if not ensure_logged_in(page, context, USERNAME, PASSWORD):
                    raise RuntimeError("Re-login échoué")
            log.warning(f"Tentative {attempt + 1}/3 échouée : {url}")
        if not loaded:
            return False

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
