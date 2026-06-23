"""
Scraper Sider (mode : catalogue léger COMPLET via sitemap + HTTP authentifié).

Énumère TOUT le catalogue via `sider_sitemap` (~23 900 fiches `/produit/`), puis
récupère chaque fiche par **GET HTTP authentifié** (cookies de session) et parse :
  - le **JSON-LD** (`<script type="application/ld+json">`) → nom, marque, catégorie,
    description, image, disponibilité ;
  - `span.article-code[data-code]` → référence article (`product_reference_fournisseur`) ;
  - `div.container-price span.price` → **prix compte P6 net HT** (≠ prix public du
    JSON-LD, souvent ~−58 %) → `normalize_price`.

Tout en HTTP concurrent (ThreadPool), **sans navigateur par produit** : ~minutes au
lieu de jours de DOM Playwright. Un seul login Playwright au départ pour obtenir les
cookies (réutilise `SiderProductScraper._ensure_session`). Les déclinaisons détaillées
(table articles) restent du ressort du mode DOM « Produits ».

Factory GUI : ``create_scraper() -> SiderLightSitemapScraper``.
CLI         : ``python scrap_sider_light_sitemap.py [--limit N]``.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from core.config import PROFILES_DIR
from core.logger import setup_logger
from core.polite_http import build_browser_headers, polite_get
from core.utils import normalize_price
from db.mariadb_db import init_site_db, insert_product, update_product_fields
from scrapers.Sider_P6.products.sider_sitemap import collect_product_urls
from scrapers.Sider_P6.products.scrap_sider_products import SiderProductScraper

load_dotenv(PROJECT_ROOT / ".env")
log = setup_logger("sider.products")

_TIMEOUT = 30
# Concurrence BORNÉE + délai jittered (dans polite_get) = anti-détection : pas de
# rafale. Le débit effectif ≈ _MAX_WORKERS / délai_moyen (~6/0,6 ≈ 10 req/s).
_MAX_WORKERS = 6
_REFERER = "https://www.sider.biz/"


class SiderLightSitemapScraper:
    """Catalogue léger COMPLET Sider : sitemap + GET HTTP authentifié (JSON-LD + prix compte)."""

    def __init__(self, limit: int | None = None) -> None:
        self._limit = limit
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    async def run(self) -> None:
        cookie, ua = await self._get_cookies()
        if not cookie:
            log.error("Session Sider indisponible — arrêt.")
            return
        await asyncio.to_thread(self._sync_fetch, cookie, ua)

    # ─── Session (1 login Playwright → cookies + UA) ──────────────────────────

    async def _get_cookies(self) -> tuple[str, str]:
        """Connexion via ``SiderProductScraper`` → (en-tête Cookie, User-Agent de session).

        On récupère AUSSI l'``User-Agent`` du navigateur de login pour que les GET
        HTTP suivants soient cohérents avec les cookies (anti-détection).
        """
        sc = SiderProductScraper()
        storage_path = PROFILES_DIR / "sider" / "session.json"
        storage = str(storage_path) if storage_path.exists() else None
        raw: list[dict] = []
        ua = ""
        try:
            await sc.start_browser(headless=True, storage_state=storage)
            page = await sc.new_page()
            await sc._ensure_session(page, storage_path)
            raw = await sc._context.cookies()
            ua = await page.evaluate("() => navigator.userAgent")
        except Exception as exc:
            log.error("Connexion Sider échouée : %s", exc)
            return "", ""
        finally:
            try:
                await sc.close()
            except Exception:
                pass
        cookie = "; ".join(f"{c['name']}={c['value']}" for c in raw if "sider" in (c.get("domain") or ""))
        return cookie, ua

    # ─── Parsing fiche ────────────────────────────────────────────────────────

    def _parse(self, html: str, url: str) -> dict | None:
        soup = BeautifulSoup(html, "html.parser")
        name = desc = brand = category = image = avail = ""
        tag = soup.find("script", attrs={"type": "application/ld+json"})
        if tag and tag.string:
            try:
                data = json.loads(tag.string)
                items = data if isinstance(data, list) else [data]
                prod = next((x for x in items if isinstance(x, dict) and x.get("@type") == "Product"), None)
                if prod:
                    name = prod.get("name", "") or ""
                    desc = prod.get("description", "") or ""
                    b = prod.get("brand")
                    brand = b.get("name", "") if isinstance(b, dict) else (b or "")
                    category = prod.get("category", "") or ""
                    img = prod.get("image", "")
                    image = img if isinstance(img, str) else (img[0] if isinstance(img, list) and img else "")
                    avail = ((prod.get("offers") or {}).get("availability") or "").rsplit("/", 1)[-1]
            except Exception:
                pass
        ac = soup.select_one("span.article-code")
        ref = ((ac.get("data-code") or ac.get_text(strip=True)) if ac else "").strip()
        pe = soup.select_one("div.container-price span.price")
        price = normalize_price(pe.get_text()) if pe else ""
        if not (ref or name):
            return None
        cat_tree = "||".join(p.strip() for p in category.split("/") if p.strip())
        stock = "disponible" if avail == "InStock" else ("non disponible" if avail else "")
        return {
            "product_fournisseur":           "P6",
            "product_reference_fournisseur": ref,
            "product_designation":           name,
            "product_description":           desc,
            "product_brand":                 brand,
            "product_category_tree":         cat_tree,
            "product_image_url":             image,
            "product_price_ht":              price,
            "product_stock_status":          stock,
            "product_fournisseur_url":       url,
        }

    def _fetch_one(self, url: str, headers: dict) -> dict | None:
        # polite_get : délai jittered + retry/back-off sur rate-limit (anti-détection)
        html = polite_get(url, headers, timeout=_TIMEOUT,
                          should_stop=lambda: self._stop_requested)
        if not html:
            return None
        return self._parse(html, url)

    def _persist(self, row: dict) -> str:
        """UPDATE si la réf existe (préserve les champs riches DOM), sinon INSERT."""
        ref = row.get("product_reference_fournisseur", "")
        if ref and update_product_fields(None, "sider", ref, row):
            return "maj"
        try:
            insert_product(None, "sider", row)
        except Exception:
            pass
        return "new"

    # ─── Boucle HTTP concurrente ──────────────────────────────────────────────

    def _sync_fetch(self, cookie: str, ua: str) -> None:
        try:
            init_site_db("sider")
        except Exception as exc:
            log.error("Base MariaDB Sider non initialisée : %s", exc)
            return
        urls = collect_product_urls(logger=log, limit=self._limit)
        if not urls:
            log.warning("Aucune URL produit (sitemap) — arrêt.")
            return
        log.info("Sider : %d produit(s) à enrichir (HTTP poli, %d workers)",
                 len(urls), _MAX_WORKERS)
        headers = build_browser_headers(ua=ua, cookie=cookie, referer=_REFERER)
        ok = miss = 0
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futs = {ex.submit(self._fetch_one, u, headers): u for u in urls}
            try:
                for fut in as_completed(futs):
                    if self._stop_requested:
                        log.info("Arrêt demandé.")
                        break
                    row = fut.result()
                    if row:
                        self._persist(row)
                        ok += 1
                    else:
                        miss += 1
                    if (ok + miss) % 250 == 0:
                        log.info("  %d/%d traités (%d enrichis, %d sans données)",
                                 ok + miss, len(urls), ok, miss)
            finally:
                ex.shutdown(wait=False, cancel_futures=True)
        log.info("Terminé : %d enrichis, %d sans données", ok, miss)


def create_scraper() -> SiderLightSitemapScraper:
    """Fabrique attendue par la GUI."""
    return SiderLightSitemapScraper()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Catalogue léger COMPLET Sider (sitemap + HTTP authentifié).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limiter le nombre de produits (test).")
    args = parser.parse_args()
    asyncio.run(SiderLightSitemapScraper(limit=args.limit).run())


if __name__ == "__main__":
    main()
