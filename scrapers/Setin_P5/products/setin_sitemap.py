"""
Énumération du catalogue Setin via le **sitemap XML** (P5).

``robots.txt`` déclare ``Sitemap: https://www.setin.fr/sitemap-index.xml``.
Contrairement à Sider (urlset unique), Setin publie un **index** à descendre :

    sitemap-index.xml
      ├── siteMapsFRCategorie1.xml   (catégories — ignoré)
      ├── siteMapsFRImage1.xml, …    (images — ignoré)
      ├── siteMapsFRPage1.xml        (CMS — ignoré)
      └── siteMapsFRProduit1.xml     ← ~20 000 fiches, toutes avec ``<lastmod>``

Source **autoritaire** pour énumérer tout le catalogue, **sans login ni
navigateur** — là où le crawl du menu 3 niveaux (voie « catégories » historique,
``scrap_setin_products.py``) est lent (jusqu'à 60 clics « voir plus » par
catégorie) et de couverture invérifiable.

Le ``<lastmod>`` alimente le filtre incrémental ``depuis``.

⚠️ Ne pas décoder les octets à la main : le XML porte sa propre déclaration
d'encodage et ``ElementTree`` s'en sert. C'est l'**HTML des fiches** qui est en
iso-8859-1, pas le sitemap (cf. ``setin_fiche_json.decoder``).

**stdlib pure** (urllib + xml.etree). Le parsing est séparé du fetch → testable
sur fixtures sans réseau.
"""

from __future__ import annotations

import gzip
import io
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator

_log = logging.getLogger(__name__)

BASE_URL = "https://www.setin.fr"
SITEMAP_INDEX_URL = f"{BASE_URL}/sitemap-index.xml"

# UA réaliste : un UA « bot » (défaut urllib) se fait servir des 403 par bien des CDN.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_HTTP_TIMEOUT = 90

# Sous-sitemaps de FICHES. Le motif tolère un préfixe de langue variable et une
# numérotation multiple (Produit1, Produit2… si le catalogue grossit au-delà de la
# limite d'un fichier) — mais exclut Categorie / Image / Page.
_RE_SITEMAP_PRODUIT = re.compile(r"siteMaps[A-Za-z]*Produit\d*\.xml$", re.I)


# ─── Parsing (pur) ────────────────────────────────────────────────────────────

def _local(tag: str) -> str:
    """Nom local d'une balise namespacée : ``'{…/0.9}loc'`` → ``'loc'``."""
    return tag.rsplit("}", 1)[-1]


def entree(url_elem: ET.Element) -> dict:
    """Extrait ``{url, lastmod}`` d'un élément ``<url>``. **Pur**."""
    loc = lastmod = None
    for enfant in url_elem:
        nom = _local(enfant.tag)
        txt = (enfant.text or "").strip()
        if nom == "loc" and txt:
            loc = txt
        elif nom == "lastmod" and txt:
            lastmod = txt
    return {"url": loc, "lastmod": lastmod}


def parser_index(data: bytes) -> list[str]:
    """URLs des sous-sitemaps déclarés par un ``<sitemapindex>``. **Pur**."""
    racine = ET.fromstring(data)  # noqa: S314 — sitemap du fournisseur, pas une entrée tierce
    locs: list[str] = []
    for element in racine.iter():
        if _local(element.tag) == "loc":
            texte = (element.text or "").strip()
            if texte:
                locs.append(texte)
    return locs


def sitemaps_produit(locs: list[str]) -> list[str]:
    """Ne garde que les sous-sitemaps de **fiches produit**. **Pur**.

    Les sitemaps Catégorie / Image / Page contiennent des URLs qui ne sont pas des
    fiches : les inclure ferait fetcher des milliers de pages sans table de variantes.
    """
    return [loc for loc in locs if _RE_SITEMAP_PRODUIT.search(loc)]


def parser_urlset(data: bytes) -> Iterator[dict]:
    """Streame les entrées d'un ``urlset``. Mémoire bornée (iterparse). **Pur**."""
    contexte = ET.iterparse(io.BytesIO(data), events=("start", "end"))
    _, racine = next(contexte)
    for evenement, element in contexte:
        if evenement == "end" and _local(element.tag) == "url":
            valeur = entree(element)
            if valeur["url"]:
                yield valeur
            element.clear()
            racine.clear()  # libère les enfants déjà traités


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
    if data[:2] == b"\x1f\x8b":  # gzip sans en-tête déclaré
        data = gzip.decompress(data)
    return data


def iter_entrees_produit() -> Iterator[dict]:
    """Itère toutes les fiches produit du catalogue (déduplique par URL).

    Descend l'index, ne retient que les sous-sitemaps « Produit », puis streame
    leurs ``<url>``. Un sous-sitemap illisible est **journalisé et sauté** : mieux
    vaut un run partiel bruyant qu'un abandon total du catalogue.
    """
    locs = sitemaps_produit(parser_index(_http_get(SITEMAP_INDEX_URL)))
    if not locs:
        _log.warning("Aucun sous-sitemap « Produit » dans %s.", SITEMAP_INDEX_URL)
        return

    vues: set[str] = set()
    for loc in locs:
        try:
            data = _http_get(loc)
        except Exception as exc:
            _log.warning("Sous-sitemap %s illisible (%s) — sauté.", loc, exc)
            continue
        for valeur in parser_urlset(data):
            url = valeur["url"]
            if url not in vues:
                vues.add(url)
                yield valeur
