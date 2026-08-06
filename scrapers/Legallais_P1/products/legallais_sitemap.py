"""
Énumération du catalogue Legallais via les **sitemaps XML** (P1).

Le sitemap publié est la liste autoritaire des fiches produit — et il est servi en
**HTTP simple, sans antibot ni login** : on contourne ainsi la protection pour
l'énumération, là où le crawl par catégories passe par le rendu navigateur.

    /sitemap.xml (index) → sitemap.products.1..N.xml (urlsets) ≈ 48 000 fiches

⚠️ Chaque ``<url>`` ne porte qu'un ``<loc>`` — **pas de ``lastmod``**, donc pas de
filtre incrémental possible côté sitemap (contrairement à Setin).

L'URL est ``/produit/{slug}/{id}`` : le dernier segment est l'**id de page**, qui
sert de référence de fiche à l'énumération. Les vraies références article
(multiples par fiche) arrivent à l'enrichissement.

⚠️ **Le sitemap ne suffit pas à couvrir le catalogue** : les fiches « gamme »
chargent leur tableau d'articles en JS et ne rendent aucun code en statique. Cette
voie est une passe d'**enrichissement rapide**, pas un remplacement du parcours
par catégories.

**stdlib pure** (urllib + xml.etree) : parsing séparé du fetch, testable sur
fixtures sans réseau.
"""

from __future__ import annotations

import gzip
import io
import logging
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from urllib.parse import urlsplit

_log = logging.getLogger(__name__)

BASE_URL = "https://www.legallais.com"
SITEMAP_INDEX = f"{BASE_URL}/sitemap.xml"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_HTTP_TIMEOUT = 90

# Feuilles à retenir depuis l'index : uniquement la branche produits (on ignore
# catégories et guides, hors périmètre de l'énumération de fiches).
_MARQUEUR_PRODUIT = "products"


# ─── Parsing (pur) ────────────────────────────────────────────────────────────

def _local(tag: str) -> str:
    """Nom local d'une balise namespacée : ``'{…/0.9}loc'`` → ``'loc'``."""
    return tag.rsplit("}", 1)[-1]


def classer_document(data: bytes) -> tuple[str, list[str]]:
    """Classe un document sitemap. **Pur**.

    ``("urlset", [])`` pour une feuille produit (on ne parse pas la feuille ici :
    on s'arrête à la racine), ou ``("index", [locs…])`` pour un index.
    """
    contexte = ET.iterparse(io.BytesIO(data), events=("start", "end"))
    _, racine = next(contexte)  # premier 'start' = élément racine
    if _local(racine.tag) == "urlset":
        return "urlset", []
    locs: list[str] = []
    for evenement, element in contexte:
        if evenement == "end" and _local(element.tag) == "loc" and (element.text or "").strip():
            locs.append(element.text.strip())
    return "index", locs


def iter_locs(data: bytes) -> Iterator[str]:
    """Streame les ``<loc>`` d'une feuille ``urlset`` (mémoire bornée). **Pur**."""
    contexte = ET.iterparse(io.BytesIO(data), events=("start", "end"))
    _, racine = next(contexte)
    for evenement, element in contexte:
        if evenement == "end" and _local(element.tag) == "url":
            loc = (element.findtext("{*}loc") or element.findtext("loc") or "").strip()
            if loc:
                yield loc
            element.clear()
            racine.clear()


def ref_depuis_url(url: str) -> str:
    """Référence = **dernier segment numérique** de l'URL, ``""`` sinon. **Pur**.

    Les fiches sont ``/produit/<slug>/<id>`` : le dernier segment identifie la
    page. Source fiable — le DOM ``c-reference`` est absent des fiches « plus
    disponible ».
    """
    if not url:
        return ""
    dernier = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return dernier if dernier.isdigit() else ""


# ─── Fetch (réseau) ───────────────────────────────────────────────────────────

def _http_get(url: str, timeout: int = _HTTP_TIMEOUT) -> bytes:
    """GET d'une URL (gère gzip, déclaré ou non). Retourne le corps brut."""
    requete = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "application/xml,text/xml,*/*",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(requete, timeout=timeout) as reponse:  # noqa: S310 — https connu
        data = reponse.read()
        if (reponse.headers.get("Content-Encoding") or "").lower() == "gzip":
            data = gzip.decompress(data)
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def decouvrir_feuilles_produit() -> list[str]:
    """Index ``/sitemap.xml`` → feuilles ``sitemap.products.*.xml``."""
    try:
        data = _http_get(SITEMAP_INDEX)
    except Exception as exc:
        _log.warning("Index sitemap injoignable %s : %s", SITEMAP_INDEX, exc)
        return []
    genre, enfants = classer_document(data)
    if genre == "urlset":  # index servi comme urlset : on le prend tel quel
        return [SITEMAP_INDEX]
    return [loc for loc in enfants if _MARQUEUR_PRODUIT in loc.lower()]


def iter_entrees_produit(feuilles: list[str] | None = None) -> Iterator[dict]:
    """Itère toutes les entrées produit (déduplique par URL). ``{"url": …}``."""
    if feuilles is None:
        feuilles = decouvrir_feuilles_produit()
        _log.info("Sitemaps produit découverts : %d", len(feuilles))
    vues: set[str] = set()
    for i, feuille in enumerate(feuilles, 1):
        _log.info("  [%d/%d] lecture %s", i, len(feuilles), feuille)
        try:
            for url in iter_locs(_http_get(feuille)):
                if url not in vues:
                    vues.add(url)
                    yield {"url": url}
        except Exception as exc:
            _log.warning("Lecture sitemap échouée %s : %s", feuille, exc)
