"""
Enrichissement **prix** d'un article Legallais via ``/get-article-infos/<code>``.

La voie légère énumère les fiches (sitemap) et extrait les **codes article** de la
table de références (``legallais_fiche_html.articles_codes``). Cet endpoint donne,
**par code** et en JSON, le **prix net B2B** (tarif négocié du compte connecté)
plus le titre, la marque, l'univers et la famille — sans rendu navigateur.

Réponse ``POST /get-article-infos/<code>`` :

    {"success": true, "result": {
        "id": 190620, "code": "104802", "title": "Embout TX20 SHW…",
        "slug": "/produit/embout-tx20-shw.../89464/104802",
        "price": {"base_price": 41.59, "discount": "59.74", "net_price": 16.74},
        "brand_title": "MILWAUKEE", "category_title": "Embouts de vissage",
        "univers": "OUTILLAGE", "famille": "Outillage à main"}}

``net_price`` = prix **compte HT**, c'est le prix cible. Le **stock** n'est pas
renvoyé ici (endpoint distinct, à contexte agence — hors périmètre).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .legallais_sitemap import BASE_URL

if TYPE_CHECKING:
    from playwright.async_api import APIRequestContext

_log = logging.getLogger(__name__)

#: Endpoint prix par code article (POST, réponse JSON). Session rejouée par cookies.
ENDPOINT = f"{BASE_URL}/get-article-infos/"


def _prix_net(result: dict) -> str:
    """Prix net HT en chaîne « point-décimal », ou ``""`` si absent. **Pur**."""
    net = (result.get("price") or {}).get("net_price")
    return "" if net in (None, "") else str(net)


def _categories(result: dict, base: dict) -> list[str]:
    """Fil d'Ariane de la page (plus complet), sinon univers > famille > catégorie."""
    if base.get("categories"):
        return base["categories"]
    hierarchie = (result.get("univers"), result.get("famille"), result.get("category_title"))
    return [c for c in hierarchie if c]


def mapper_article(result: dict, base: dict, *, stock: str = "") -> dict:
    """Extrait d'article + infos article → extrait pour ``core.f2.element_produit``. **Pur**.

    L'article est l'**unité vendable** : sa référence est le **code** article, et
    sa désignation comme son prix priment sur ceux de la page (niveau gamme). On
    conserve de la page l'image et le fil d'Ariane. L'URL devient celle de
    l'article quand le slug est fourni — c'est ce qui donne à chaque article une
    identité ``product_uid`` distincte.

    ⚠️ ``base`` est l'extrait **de l'article** produit par
    ``legallais_fiche_html.fiche_et_articles``, pas la base page : c'est lui qui
    porte la **référence fabricant** de la déclinaison, que cet endpoint ne
    renvoie pas et qu'on ne doit donc jamais écraser ici.
    """
    extrait = dict(base)  # image + catégories de la page repris tels quels
    extrait["ref"] = str(result.get("code") or "")
    extrait["designation"] = result.get("title") or base.get("designation") or ""
    extrait["prix"] = _prix_net(result)
    extrait["marque"] = result.get("brand_title") or base.get("marque") or ""
    extrait["categories"] = _categories(result, base)
    if stock or not extrait.get("stock"):
        extrait["stock"] = stock
    slug = result.get("slug") or ""
    if slug:
        extrait["url"] = f"{BASE_URL}{slug}" if slug.startswith("/") else slug
    return extrait


async def recuperer_article(request: APIRequestContext, code: str, *,
                            entetes: dict[str, str],
                            timeout_ms: int = 20_000) -> dict | None:
    """``result`` de ``/get-article-infos/<code>``, ou ``None`` si échec. **Live**.

    Réutilise la session (cookies du contexte) pour obtenir le prix compte.
    Renvoie ``None`` — l'appelant garde alors la base page — sur erreur réseau,
    statut non-OK, JSON illisible ou ``success != true``.
    """
    entetes_xhr = {**entetes, "X-Requested-With": "XMLHttpRequest"}
    try:
        reponse = await request.post(f"{ENDPOINT}{code}", headers=entetes_xhr,
                                     timeout=timeout_ms)
        if not reponse.ok:
            _log.debug("get-article-infos %s : statut %s", code, reponse.status)
            return None
        donnees = await reponse.json()
    except Exception as exc:
        _log.warning("get-article-infos %s échoué : %s", code, exc)
        return None
    if not isinstance(donnees, dict) or not donnees.get("success"):
        return None
    result = donnees.get("result")
    return result if isinstance(result, dict) else None
