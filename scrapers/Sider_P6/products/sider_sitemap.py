"""
Énumération du catalogue Sider via le sitemap (fournisseur P6).

Le site publie un **urlset XML unique** (pas un index) à une URL non standard :
``https://www.sider.biz/plan-du-site`` (``/sitemap.xml`` renvoie 404). Il contient
~24 500 ``<url>`` dont ~23 900 fiches produit ``/produit/<slug>.<id>`` (le reste =
catégories + CMS), chacune avec un ``<lastmod>`` (→ scraping incrémental).

Source autoritaire et exhaustive pour énumérer tout le catalogue, **sans login ni
navigateur** — là où le crawl du méga-menu est lent et incomplet. Pendant du
``prolians_sitemap`` (en plus simple : un seul urlset, pas d'index à descendre).

Le suffixe numérique de l'URL (« …slug.100000002 ») est l'**id site** (= ``sku``
du JSON-LD), PAS la référence article Sider (celle-ci = ``span.article-code``).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import gzip
import io
import random
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator

from core.logger import setup_logger
from core.polite_http import build_browser_headers

log = setup_logger("sider.products")

SITEMAP_URL = "https://www.sider.biz/plan-du-site"
_REFERER = "https://www.sider.biz/"
_SKU_RE = re.compile(r"\.(\d+)$")


def _http_get(url: str, timeout: int = 90, retries: int = 3) -> bytes:
    """GET du sitemap avec en-têtes navigateur réalistes + retry/back-off sur 429/403/503.

    (Le sitemap est servi par un CDN qui 403 les requêtes au pattern « bot » :
    UA minimal, pas d'Accept-Language, rafales.)
    """
    headers = build_browser_headers(referer=_REFERER)
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip" or data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            return data
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 503) and attempt < retries:
                time.sleep(2.0 ** attempt + random.uniform(0.0, 1.5))
                continue
            raise
        except Exception:
            if attempt < retries:
                time.sleep(1.5 ** attempt + random.uniform(0.0, 0.5))
                continue
            raise
    raise RuntimeError("unreachable")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def iter_product_entries(logger=None) -> Iterator[dict]:
    """Streame le sitemap et yield ``{url, sku, lastmod}`` pour chaque fiche ``/produit/``."""
    lg = logger or log
    data = _http_get(SITEMAP_URL)
    ctx = ET.iterparse(io.BytesIO(data), events=("start", "end"))
    _, root = next(ctx)
    for event, elem in ctx:
        if event == "end" and _local(elem.tag) == "url":
            loc = lastmod = None
            for ch in elem:
                lt = _local(ch.tag)
                if lt == "loc":
                    loc = (ch.text or "").strip()
                elif lt == "lastmod":
                    lastmod = (ch.text or "").strip()
            if loc and "/produit/" in loc:
                m = _SKU_RE.search(loc)
                yield {"url": loc, "sku": m.group(1) if m else "", "lastmod": lastmod}
            elem.clear()
            root.clear()


def collect_product_urls(logger=None, limit: int | None = None) -> list[str]:
    """Liste (dédupliquée, ordonnée) des URLs produit du catalogue."""
    seen: set[str] = set()
    out: list[str] = []
    for entry in iter_product_entries(logger):
        u = entry["url"]
        if u not in seen:
            seen.add(u)
            out.append(u)
            if limit and len(out) >= limit:
                break
    return out


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Énumération catalogue Sider via sitemap.")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    urls = collect_product_urls(limit=args.limit)
    log.info("Catalogue Sider : %d URL(s) produit", len(urls))
    for u in urls[:10]:
        log.info("  %s", u)


if __name__ == "__main__":
    main()
