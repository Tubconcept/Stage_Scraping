"""
Complétion des tarifs Setin manquants via ``POST /ajax/load_prices.php`` (P5).

**Le point dur de la voie légère.** Sur une fiche à nombreuses variantes,
``json_tarifs`` ne contient que les **10 premières** (``price_packet`` vaut 10
dans ``web.all.js``) ; les autres sont listées dans ``json_variantes_to_sync`` et
chargées en AJAX par le site lui-même :

    article.all.js : if (typeof json_variantes_to_sync != 'undefined')
                       { syncPrices(json_variantes_to_sync, 'fiche_article') }
    web.all.js     : articles = aIds.splice(0, price_packet)
                     $.ajax({url:'/ajax/load_prices.php', type:'POST', …})

On rejoue exactement cet appel. Vérifié en réel sur une fiche à 29 variantes
(10 tarifs inline + 19 à synchroniser) : 19/19 récupérés, tous à ``basePrice > 0``.

Sans cette complétion, **une variante sur trois du catalogue partirait sans
prix** — et en silence, ce qui est le pire des cas.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from .setin_fiche_json import decoder

if TYPE_CHECKING:
    from playwright.async_api import APIRequestContext

_log = logging.getLogger(__name__)

URL_LOAD_PRICES = "https://www.setin.fr/ajax/load_prices.php"

#: Taille de paquet du site (``$('body').data('price_packet') || 10``). En demander
#: davantage d'un coup, c'est s'écarter du trafic normal pour un gain nul.
TAILLE_PAQUET = 10

ORIGINE_FICHE = "fiche_article"


def paquets(ids: list[int], taille: int = TAILLE_PAQUET) -> list[list[int]]:
    """Découpe les ids en paquets de ``taille``. **Pur**."""
    if taille < 1:
        raise ValueError("La taille de paquet doit être >= 1.")
    return [ids[debut:debut + taille] for debut in range(0, len(ids), taille)]


def corps_formulaire(ids: list[int], origine: str = ORIGINE_FICHE) -> str:
    """Corps ``application/x-www-form-urlencoded`` attendu par le site. **Pur**.

    jQuery sérialise un tableau en ``ids[]=1&ids[]=2``, crochets percent-encodés
    (``%5B%5D``). Reproduit tel quel : le PHP en face lit ``$_POST['ids']`` comme
    un tableau, et ne le verrait pas avec un simple ``ids=1,2``.
    """
    paires = [f"ids%5B%5D={int(i)}" for i in ids]
    paires.append(f"from={origine}")
    return "&".join(paires)


def _entetes(referer: str) -> dict[str, str]:
    """En-têtes d'un appel AJAX jQuery depuis la fiche."""
    return {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }


def tarifs_depuis_reponse(corps: str) -> dict:
    """``{"tarifs": {…}}`` → le dict de tarifs, ``{}`` si inexploitable. **Pur**."""
    try:
        charge = json.loads(corps)
    except (json.JSONDecodeError, TypeError):
        return {}
    tarifs = charge.get("tarifs") if isinstance(charge, dict) else None
    return tarifs if isinstance(tarifs, dict) else {}


async def completer(request: APIRequestContext, ids: list[int], *,
                    referer: str, timeout_ms: int = 20_000) -> dict:
    """Récupère les tarifs des variantes ``ids``. Retourne ``{id: tarif}``.

    Un paquet en échec est **journalisé et sauté** : les variantes concernées
    partiront sans prix, ce qui est visible côté PIM — au lieu de faire perdre la
    fiche entière.
    """
    if not ids:
        return {}

    obtenus: dict = {}
    entetes = _entetes(referer)
    for paquet in paquets(ids):
        try:
            reponse = await request.post(
                URL_LOAD_PRICES, data=corps_formulaire(paquet),
                headers=entetes, timeout=timeout_ms,
            )
        except Exception as exc:
            _log.warning("load_prices %s : %s", referer, exc)
            continue
        if not reponse.ok:
            _log.info("load_prices %s : statut %s", referer, reponse.status)
            continue
        corps = decoder(await reponse.body(), reponse.headers.get("content-type"))
        obtenus.update(tarifs_depuis_reponse(corps))

    manquants = [i for i in ids if str(i) not in obtenus]
    if manquants:
        _log.info("Tarifs non obtenus pour %d variante(s) de %s.", len(manquants), referer)
    return obtenus
