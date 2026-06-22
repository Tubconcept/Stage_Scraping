"""
Énumération du catalogue Prolians via les **sitemaps XML** (fournisseur P3).

Pourquoi ce module ?
  Le crawl par catégories (``scrap_prolians_by_category``) ne **garantit pas la
  complétude** : méga-menu React impilotable, plafond de pages par catégorie,
  produits non rattachés à une feuille de l'arbre. Le sitemap publié par le site
  est au contraire la **liste autoritaire et exhaustive** de toutes les fiches
  produit — c'est le site lui-même qui l'expose pour les moteurs de recherche.

Chaîne de découverte (robuste aux changements d'URL) :

    /robots.txt   →  Sitemap: …/sitemap.xml          (index)
                  →  …/product_sitemap.xml            (index)
                  →  …/product_sitemap-1-N.xml        (urlsets : ~9 650 produits chacun)

Chaque entrée ``<url>`` fournit, **sans login ni navigateur** :
  - ``url``     : URL de la fiche produit (= ``urlKey``)
  - ``lastmod`` : date de dernière modification → scraping **incrémental**
  - ``name``    : libellé produit (extrait de ``<image:title>``)
  - ``images``  : URLs d'images (``<image:loc>``)

Tout est en **stdlib pure** (urllib + xml.etree) : aucune dépendance, aucun
navigateur. L'énumération seule ne touche pas la base ; ``--audit`` importe la
couche MariaDB pour comparer le sitemap à ce qui est déjà scrappé.

Usage :
  - bibliothèque : ``collect_product_urls()``, ``iter_product_entries()``
  - CLI          : ``python scrapers/Prolians_P3/products/prolians_sitemap.py``
        --stats              compte les produits du sitemap + ventilation des réfs
        --out FICHIER        écrit la liste des URLs (1 par ligne)
        --audit              compare au contenu de la base (URLs manquantes/orphelines)
        --audit --out F.txt  écrit dans F.txt les URLs présentes au sitemap mais
                             ABSENTES de la base (= produits à scraper)
        --limit N            s'arrête après N produits (test rapide)
"""
from __future__ import annotations

import sys
from pathlib import Path

# --- Racine projet (css_selectors, core, db) ---
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import gzip
import io
import re
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator

from css_selectors.prolians import Selectors
from core.logger import setup_logger

log = setup_logger("prolians.sitemap")

ROBOTS_URL = f"{Selectors.BASE_URL}/robots.txt"

# En-tête de navigateur réaliste — certains CDN renvoient 403 à l'UA urllib par défaut.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Espace de noms des images dans le sitemap (Google image sitemap extension).
_IMAGE_NS = "sitemap-image"

_HTTP_TIMEOUT = 90


# =============================================================================
# HTTP + parsing bas niveau (stdlib)
# =============================================================================

def _http_get(url: str, timeout: int = _HTTP_TIMEOUT) -> bytes:
    """GET d'une URL, gère la décompression gzip. Retourne le corps brut."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "application/xml,text/xml,*/*",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
            data = gzip.decompress(data)
    # Repli : certains serveurs renvoient du gzip sans l'en-tête (magie 1f 8b).
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def _local(tag: str) -> str:
    """Nom local d'une balise namespacée : '{…/0.9}loc' -> 'loc'."""
    return tag.rsplit("}", 1)[-1]


def _sitemaps_from_robots(logger=None) -> list[str]:
    """Lit les directives ``Sitemap:`` du robots.txt (source la plus robuste)."""
    try:
        body = _http_get(ROBOTS_URL).decode("utf-8", "replace")
    except Exception as exc:
        if logger:
            logger.warning("robots.txt injoignable (%s) — repli sur SITEMAP_INDEX", exc)
        return []
    return re.findall(r"(?im)^\s*Sitemap:\s*(\S+)\s*$", body)


def _read_index(data: bytes) -> tuple[str, list[str]]:
    """Classe un document sitemap et, si c'est un index, liste ses enfants.

    Retourne ``("urlset", [])`` pour une feuille de produits (on ne parse alors
    PAS les ~10 Mo : on s'arrête au tout premier événement, la balise racine),
    ou ``("index", [locs…])`` pour un index (fichiers petits → parse complet).
    """
    ctx = ET.iterparse(io.BytesIO(data), events=("start", "end"))
    _, root = next(ctx)  # premier 'start' = élément racine
    if _local(root.tag) == "urlset":
        return "urlset", []
    locs: list[str] = []
    for event, elem in ctx:
        if event == "end" and _local(elem.tag) == "loc" and (elem.text or "").strip():
            locs.append(elem.text.strip())
    return "index", locs


# =============================================================================
# Découverte des sitemaps produit
# =============================================================================

def discover_product_sitemaps(logger=None) -> list[str]:
    """robots.txt → index(s) → feuilles ``urlset`` de la branche **produit**.

    Ne descend que dans les sous-sitemaps dont l'URL contient « product » (on
    ignore ``cms_sitemap``, ``arborescence_sitemap``, ``custom_sitemap`` qui ne
    sont pas des fiches produit). Retourne la liste des URLs de feuilles.
    """
    roots = _sitemaps_from_robots(logger) or [Selectors.SITEMAP_INDEX]
    leaves: list[str] = []
    seen: set[str] = set()
    queue: list[str] = list(roots)
    is_root = set(roots)

    while queue:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        try:
            data = _http_get(u)
        except Exception as exc:
            if logger:
                logger.warning("Sitemap injoignable %s : %s", u, exc)
            continue
        kind, child_locs = _read_index(data)
        if kind == "urlset":
            leaves.append(u)
            continue
        for loc in child_locs:
            low = loc.lower()
            # Depuis la racine on ne garde que la branche produit ; aux niveaux
            # suivants tous les enfants sont déjà des sitemaps produit.
            if u in is_root and not ("product" in low and "sitemap" in low):
                continue
            if loc not in seen:
                queue.append(loc)
    return leaves


# =============================================================================
# Itération des entrées produit
# =============================================================================

def _entry(url_elem: ET.Element) -> dict:
    """Extrait (url, lastmod, name, images) d'un élément ``<url>``."""
    loc = lastmod = name = None
    images: list[str] = []
    for child in url_elem.iter():
        lt = _local(child.tag)
        txt = (child.text or "").strip()
        if lt == "loc":
            if _IMAGE_NS in child.tag:
                if txt:
                    images.append(txt)
            elif txt:
                loc = txt
        elif lt == "lastmod" and txt:
            lastmod = txt
        elif lt == "title" and _IMAGE_NS in child.tag and not name and txt:
            name = txt
    return {"url": loc, "lastmod": lastmod, "name": name, "images": images}


def _iter_urlset_entries(url: str, logger=None) -> Iterator[dict]:
    """Streame les entrées d'une feuille ``urlset`` (mémoire bornée)."""
    data = _http_get(url)
    ctx = ET.iterparse(io.BytesIO(data), events=("start", "end"))
    _, root = next(ctx)
    for event, elem in ctx:
        if event == "end" and _local(elem.tag) == "url":
            entry = _entry(elem)
            if entry["url"]:
                yield entry
            elem.clear()
            root.clear()  # libère les enfants déjà traités


def iter_product_entries(leaves: list[str] | None = None, logger=None) -> Iterator[dict]:
    """Itère toutes les entrées produit de tous les sitemaps (déduplique les URLs)."""
    lg = logger or log
    if leaves is None:
        leaves = discover_product_sitemaps(lg)
        lg.info("Sitemaps produit découverts : %d", len(leaves))
    seen: set[str] = set()
    for i, leaf in enumerate(leaves, 1):
        lg.info("  [%d/%d] lecture %s", i, len(leaves), leaf)
        try:
            for entry in _iter_urlset_entries(leaf, lg):
                u = entry["url"]
                if u not in seen:
                    seen.add(u)
                    yield entry
        except Exception as exc:
            lg.warning("Lecture sitemap échouée %s : %s", leaf, exc)


def collect_product_urls(leaves: list[str] | None = None, logger=None,
                         limit: int | None = None) -> list[str]:
    """Retourne la liste (dédupliquée, ordonnée) des URLs produit du catalogue."""
    out: list[str] = []
    for entry in iter_product_entries(leaves, logger):
        out.append(entry["url"])
        if limit and len(out) >= limit:
            break
    return out


# =============================================================================
# Audit : sitemap vs contenu de la base
# =============================================================================

def _norm_url(u: str) -> str:
    """Normalise pour comparer (retire query/fragment et slash final)."""
    u = (u or "").split("#", 1)[0].split("?", 1)[0]
    return u.rstrip("/")


def audit_against_db(site: str = "prolians", leaves: list[str] | None = None,
                     logger=None) -> dict:
    """Compare l'ensemble des URLs du sitemap à celles déjà présentes en base.

    Retourne un dict de comptes + échantillons :
      - ``sitemap``  : nb d'URLs produit au sitemap (catalogue réel)
      - ``en_base``  : nb d'URLs déjà scrappées
      - ``manquant`` : au sitemap mais PAS en base (= produits à scraper)
      - ``orphelin`` : en base mais PAS au sitemap (retirés / variantes / host ≠)
      - ``manquant_urls`` : la liste complète des URLs manquantes (pour --out)
    """
    lg = logger or log
    from db.mariadb_db import get_scraped_product_urls  # import tardif (pymysql)

    sm = {_norm_url(u) for u in collect_product_urls(leaves, lg)}
    db = {_norm_url(u) for u in get_scraped_product_urls(None, site)}
    manquant = sorted(sm - db)
    orphelin = sorted(db - sm)
    return {
        "sitemap": len(sm),
        "en_base": len(db),
        "manquant": len(manquant),
        "orphelin": len(orphelin),
        "couverture_pct": round(100 * (len(sm) - len(manquant)) / len(sm), 1) if sm else 0.0,
        "manquant_urls": manquant,
        "orphelin_sample": orphelin[:10],
    }


# =============================================================================
# Statistiques de complétude (sans base)
# =============================================================================

_NUMERIC_TOKEN = re.compile(r"-(\d{6,})$")


def compute_stats(leaves: list[str] | None = None, logger=None) -> dict:
    """Compte les produits et ventile selon la nature du suffixe d'URL.

    Le suffixe numérique (``…-10013933``) est directement la réf PROLIANS ;
    le suffixe encodé (``…-03hswte``) ne l'est pas → bon indicateur de la
    part du catalogue directement « pontable » vers GraphQL sans charger la page.
    """
    lg = logger or log
    total = with_name = with_image = numeric_token = 0
    for entry in iter_product_entries(leaves, lg):
        total += 1
        if entry["name"]:
            with_name += 1
        if entry["images"]:
            with_image += 1
        if _NUMERIC_TOKEN.search(entry["url"]):
            numeric_token += 1
    return {
        "total": total,
        "avec_nom": with_name,
        "avec_image": with_image,
        "ref_numerique_dans_url": numeric_token,
        "ref_encodee_dans_url": total - numeric_token,
    }


# =============================================================================
# CLI
# =============================================================================

def _write_urls(path: str, urls: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(urls) + ("\n" if urls else ""))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Énumération du catalogue Prolians via les sitemaps XML.")
    parser.add_argument("--stats", action="store_true",
                        help="Compter les produits + ventilation des réfs (sans base).")
    parser.add_argument("--audit", action="store_true",
                        help="Comparer le sitemap au contenu de la base MariaDB.")
    parser.add_argument("--out", metavar="FICHIER",
                        help="Écrire les URLs (catalogue, ou manquantes si --audit).")
    parser.add_argument("--limit", type=int, default=None,
                        help="S'arrêter après N produits (test).")
    args = parser.parse_args()

    leaves = discover_product_sitemaps(log)
    log.info("Feuilles produit : %d → %s", len(leaves), leaves)

    if args.audit:
        res = audit_against_db(leaves=leaves)
        log.info("AUDIT sitemap vs base :")
        log.info("  catalogue (sitemap) : %d", res["sitemap"])
        log.info("  déjà en base        : %d", res["en_base"])
        log.info("  MANQUANTS (à scraper): %d", res["manquant"])
        log.info("  orphelins (base seule): %d  ex. %s", res["orphelin"], res["orphelin_sample"])
        log.info("  couverture          : %.1f %%", res["couverture_pct"])
        if args.out:
            _write_urls(args.out, res["manquant_urls"])
            log.info("  %d URL(s) manquante(s) écrite(s) → %s", res["manquant"], args.out)
        return

    if args.stats:
        st = compute_stats(leaves=leaves)
        log.info("STATS catalogue Prolians (sitemap) :")
        for k, v in st.items():
            log.info("  %-26s : %s", k, v)
        return

    # Défaut : collecter et (optionnellement) écrire toutes les URLs.
    urls = collect_product_urls(leaves=leaves, limit=args.limit)
    log.info("Catalogue : %d URL(s) produit", len(urls))
    if args.out:
        _write_urls(args.out, urls)
        log.info("→ %s", args.out)
    else:
        for u in urls[:20]:
            log.info("  %s", u)
        if len(urls) > 20:
            log.info("  … (+%d)", len(urls) - 20)


if __name__ == "__main__":
    main()
