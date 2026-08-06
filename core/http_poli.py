"""
Fetch HTTP **poli** au-dessus de l'``APIRequestContext`` Playwright.

Beaucoup de fournisseurs servent tout leur catalogue en HTTP simple : une fois
l'URL connue (sitemap), lire la fiche ne demande **aucun rendu navigateur**.
Mais un site protégé bloque l'IP sur un motif de bot (UA minimal, rafales). Ce
module centralise la politesse qui évite de déclencher ce blocage.

Différence avec ``core/polite_http.py`` : celui-là est synchrone et bâti sur
``urllib`` (fetchers hors session). Celui-ci passe par ``contexte.request``, donc
**rejoue les cookies de session** — c'est ce qui donne accès aux prix compte
sans ouvrir une page par fiche.

  - ``entetes_navigateur(ua)``          — en-têtes Chrome réalistes (**pur**)
  - ``delai_avant_reessai(status, n)``  — back-off exponentiel (**pur**)
  - ``get_poli`` / ``get_poli_octets``  — GET avec jitter + réessais bornés
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import APIRequestContext

_log = logging.getLogger(__name__)

# UA Chrome par défaut si la session n'en fournit pas (cohérence UA ↔ cookies).
UA_DEFAUT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Statuts « ralentis-toi / bloqué transitoire » qui méritent un réessai temporisé.
STATUTS_REESSAI = frozenset({403, 429, 503})

_BACKOFF_BASE = 1.5
_BACKOFF_MAX = 30.0


def entetes_navigateur(ua: str | None = None) -> dict[str, str]:
    """En-têtes d'un vrai Chrome (réduit le risque de 403 sur motif bot). **Pur**.

    Passer l'``ua`` **de la session** (``navigator.userAgent`` du navigateur qui
    a obtenu les cookies) garde la cohérence UA ↔ cookies ; sinon ``UA_DEFAUT``.
    """
    return {
        "User-Agent": ua or UA_DEFAUT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }


def delai_avant_reessai(status: int, essai: int) -> float | None:
    """Délai de back-off avant le prochain essai, ``None`` si on ne réessaie pas. **Pur**.

    ``essai`` = numéro de la tentative qui vient d'échouer (0 = première).
    """
    if status not in STATUTS_REESSAI:
        return None
    return min(_BACKOFF_BASE * (2**essai), _BACKOFF_MAX)


async def _dormir(secondes: float) -> None:
    """Point d'attente isolé (surchargeable en test pour ne pas attendre en vrai)."""
    await asyncio.sleep(secondes)


async def _get_poli(
    request: APIRequestContext,
    url: str,
    *,
    entetes: dict[str, str],
    timeout_ms: int,
    essais: int,
    jitter: tuple[float, float],
    lecteur: Callable[[Any], Awaitable[Any]],
) -> Any | None:
    """Boucle polie partagée : jitter + réessais bornés ; ``lecteur`` lit le corps.

    Le corps est lu par un **callback** parce que tous les fournisseurs ne
    servent pas de l'UTF-8 : ``reponse.text()`` convient à Legallais et Sider,
    mais lève ``UnicodeDecodeError`` sur Setin (iso-8859-1), qui a besoin des
    octets bruts. La politesse reste écrite une seule fois.
    """
    for essai in range(essais):
        await _dormir(random.uniform(*jitter))  # noqa: S311 — jitter, pas de la crypto
        try:
            reponse = await request.get(url, headers=entetes, timeout=timeout_ms)
        except Exception as exc:
            _log.warning("GET échoué %s : %s", url, exc)
            return None
        if reponse.ok:
            return await lecteur(reponse)
        attente = delai_avant_reessai(reponse.status, essai)
        if attente is None:
            _log.debug("GET %s statut %s (non réessayé)", url, reponse.status)
            return None
        _log.info("GET %s statut %s — réessai dans %.1fs", url, reponse.status, attente)
        await _dormir(attente)
    _log.warning("GET %s : abandon après %d essais", url, essais)
    return None


async def get_poli(
    request: APIRequestContext,
    url: str,
    *,
    entetes: dict[str, str],
    timeout_ms: int = 20_000,
    essais: int = 3,
    jitter: tuple[float, float] = (0.05, 0.25),
) -> str | None:
    """GET poli renvoyant le **texte**, ou ``None``.

    ⚠️ Présume un corps décodable en UTF-8 : pour un site en iso-8859-1 (Setin),
    passer par ``get_poli_octets`` et décoder soi-même.
    """
    return await _get_poli(
        request, url, entetes=entetes, timeout_ms=timeout_ms, essais=essais,
        jitter=jitter, lecteur=lambda reponse: reponse.text(),
    )


async def get_poli_octets(
    request: APIRequestContext,
    url: str,
    *,
    entetes: dict[str, str],
    timeout_ms: int = 20_000,
    essais: int = 3,
    jitter: tuple[float, float] = (0.05, 0.25),
) -> bytes | None:
    """Comme ``get_poli`` mais renvoie les **octets bruts** — au décodage de l'appelant."""
    return await _get_poli(
        request, url, entetes=entetes, timeout_ms=timeout_ms, essais=essais,
        jitter=jitter, lecteur=lambda reponse: reponse.body(),
    )
