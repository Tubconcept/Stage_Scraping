"""
Reprise après interruption — saut d'énumération.

Principe qui rend tout simple : **l'énumération est bon marché, le fetch est
cher.** Inutile de sauvegarder un état parfait ; il suffit de savoir **où
sauter**. On ré-énumère le sitemap (quelques requêtes triviales) et on saute
jusqu'au point de reprise, en évitant les milliers de fetches déjà payés.

**Checkpoint par valeur, jamais par offset** : un offset devient faux dès qu'une
fiche disparaît du catalogue entre deux tentatives — tout se décale, on saute ou
on refait du travail. Une URL, elle, reste juste.

**Repli obligatoire** : si la valeur du checkpoint a disparu de l'énumération, on
repart du **début**. Coûteux mais correct — l'écriture étant idempotente
(``product_uid``), le rejeu est sans effet. Mieux vaut un run complet bruyant
qu'un silence qui sauterait tout le catalogue.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from typing import Any

_log = logging.getLogger(__name__)


def enumerer_apres(
    fabrique: Callable[[], Iterable[Any]],
    valeur: str | None,
    cle: Callable[[Any], str],
) -> Iterator[Any]:
    """Énumère les éléments **après** ``valeur`` (énumération linéaire). **Pur**.

    Args:
        fabrique: renvoie un itérable **neuf** à chaque appel. C'est une fabrique
            et non un itérable, précisément pour pouvoir ré-énumérer au repli —
            un générateur déjà consommé ne se rembobine pas.
        valeur: point de reprise (``None``/vide = tout énumérer).
        cle: extrait d'un élément sa valeur comparable (ex. ``lambda e: e["url"]``).

    Yields:
        Les éléments **strictement après** celui dont la clé vaut ``valeur``.
        Si ``valeur`` est introuvable : **tous** les éléments (repli).
    """
    if not valeur:
        yield from fabrique()
        return

    trouve = False
    for element in fabrique():
        if trouve:
            yield element
        elif cle(element) == valeur:
            trouve = True  # l'élément lui-même est DÉJÀ traité : on ne le rejoue pas

    if not trouve:
        _log.warning("Point de reprise introuvable (%r) — run complet.", valeur)
        yield from fabrique()


def reprendre_categories(
    categories: list[str], reprise: dict | None, *, page_min: int = 1
) -> tuple[list[str], int]:
    """Où reprendre dans une énumération **imbriquée** (catégories × pages). **Pur**.

    Args:
        categories: liste ordonnée des catégories à parcourir.
        reprise: checkpoint ``{"categorie": "<url>", "page": N}`` — ``N`` étant
            la **dernière page terminée** de cette catégorie.
        page_min: numéro de la première page (1 par convention).

    Returns:
        ``(categories_restantes, page_depart)``. Les catégories suivantes
        repartent de ``page_min`` : c'est à l'appelant de n'appliquer la page de
        départ qu'à la première.
    """
    categorie = (reprise or {}).get("categorie")
    if not categorie:
        return (categories, page_min)

    try:
        index = categories.index(categorie)
    except ValueError:
        _log.warning("Catégorie de reprise introuvable (%r) — run complet.", categorie)
        return (categories, page_min)

    page = (reprise or {}).get("page")
    if page is None:
        # Catégorie connue, page inconnue : on refait la catégorie ENTIÈRE plutôt
        # que de supposer une page faite. Refaire est idempotent ; sauter perdrait
        # des données en silence.
        _log.warning("Checkpoint sans page (%r) — catégorie reprise du début.", categorie)
        return (categories[index:], page_min)

    return (categories[index:], max(page_min, int(page) + 1))
