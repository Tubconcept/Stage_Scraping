"""
Mapping d'un produit extrait → ligne au format CSV_HEADERS. **Pur**.

Commun à tous les fournisseurs (le code ``P1``/``P3``/``P5``… est un paramètre) :
base toujours présente (fournisseur, URL, référence, désignation) + champs
optionnels **non vides**. Un champ vide est omis — ``save_product`` ne l'écrit
donc pas, et n'écrase jamais une valeur déjà en base par du vide.

Porté de ``SCRAPPER_App/scrapers/commun/f2.py`` : le vocabulaire de sortie y est
déjà celui de ``core.config.CSV_HEADERS``, d'où un portage sans conversion.

⚠️ **Pas de déclinaisons** (décision 06/08/2026) : ces méthodes traitent chaque
fiche comme un **article simple**. Les colonnes ``products_is_combination``,
``product_combination_index``, ``product_combination_values``,
``product_parent_reference`` et ``product_child_reference`` ne sont jamais
produites ici. Une fiche déjà enrichie par une voie « catégories » conserve donc
ses valeurs de déclinaison : la fusion de ``save_product`` est non destructive.
"""

from __future__ import annotations

import json

from core.config import CSV_HEADERS

# Séparateurs : « > » pour le fil d'Ariane, « || » pour les listes (convention projet).
SEP_CATEGORIE = " > "
SEP_LISTE = "||"

#: Colonnes de déclinaison, volontairement jamais renseignées par les méthodes.
COLONNES_DECLINAISON = frozenset({
    "products_is_combination",
    "product_combination_index",
    "product_combination_values",
    "product_parent_reference",
    "product_child_reference",
})


def element_produit(extrait: dict, fournisseur: str) -> dict:
    """Construit la ligne produit de ``fournisseur`` (ex. ``"P5"``) depuis un extrait.

    Clés reconnues dans ``extrait``, toutes optionnelles sauf ``url`` :
    ``url``, ``ref``, ``designation``, ``prix``, ``eco``, ``stock``, ``marque``,
    ``marque_logo``, ``ean``, ``ref_fabricant``, ``description``,
    ``conditionnement``, ``promotion``, ``eco_label``, ``statut``,
    ``categories`` (list), ``images`` (list), ``docs`` (list),
    ``cross_sell`` (list), ``attributs`` (dict label→valeur).

    Returns:
        Dict aux clés de ``CSV_HEADERS`` ; les champs vides sont absents.
    """
    element = {
        "product_fournisseur": fournisseur,
        "product_fournisseur_url": extrait.get("url") or "",
        "product_reference_fournisseur": extrait.get("ref") or "",
        "product_designation": extrait.get("designation") or "",
    }
    optionnels = {
        "product_price_ht": extrait.get("prix"),
        "product_eco_taxe": extrait.get("eco"),
        "product_stock_status": extrait.get("stock"),
        "product_brand": extrait.get("marque"),
        "product_brand_logo_url": extrait.get("marque_logo"),
        "product_ean": extrait.get("ean"),
        "product_reference_fabricant": extrait.get("ref_fabricant"),
        "product_description": extrait.get("description"),
        "product_conditionnement": extrait.get("conditionnement"),
        "product_promotion": extrait.get("promotion"),
        "product_eco_label": extrait.get("eco_label"),
        "product_status": extrait.get("statut"),
    }
    element.update({cle: str(val) for cle, val in optionnels.items() if val not in (None, "")})

    listes = {
        "product_category_tree": (extrait.get("categories"), SEP_CATEGORIE),
        "product_image_url": (extrait.get("images"), SEP_LISTE),
        "product_docs_url": (extrait.get("docs"), SEP_LISTE),
        "product_cross_sell": (extrait.get("cross_sell"), SEP_LISTE),
    }
    for colonne, (valeurs, separateur) in listes.items():
        propres = [str(v) for v in (valeurs or []) if v]
        if propres:
            element[colonne] = separateur.join(propres)

    # Caractéristiques techniques → JSON (forme attendue côté PIM).
    attributs = {
        str(cle): str(valeur)
        for cle, valeur in (extrait.get("attributs") or {}).items()
        if cle and valeur
    }
    if attributs:
        element["product_attributes"] = json.dumps(attributs, ensure_ascii=False)
    return element


def colonnes_inconnues(element: dict) -> set[str]:
    """Clés de ``element`` absentes de ``CSV_HEADERS`` — garde-fou de test.

    Une clé inconnue serait ignorée en silence par ``save_product`` : mieux vaut
    qu'un test la fasse remarquer qu'un champ disparaisse sans bruit.
    """
    return set(element) - set(CSV_HEADERS)
