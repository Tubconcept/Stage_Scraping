"""
Normalisation de texte et de prix pour les méthodes de scraping. **Pur**.

Distinct de ``core/utils.clean_text`` : celui-ci supprime le ``;`` pour protéger
l'export CSV historique, ce qui **mutile les descriptions** (« Ø 40 mm ; inox »
perd son séparateur). Les méthodes écrivent en base, pas en CSV : elles ont
besoin d'un nettoyage qui préserve le texte.
"""

from __future__ import annotations

import html
import re

# Prix FR : « 1 752,90 € » → numérique. En mode Unicode, ``\s`` couvre toutes les
# variantes d'espace (normal, insécable, fine). ``(?:[.,]\d+)?`` = partie décimale.
_PRIX_RE = re.compile(r"\d[\d\s]*(?:[.,]\d+)?")


def nettoyer_texte(valeur: object) -> str:
    """Nettoie un texte extrait du web : entités HTML, espaces spéciaux, blancs."""
    if not isinstance(valeur, str):
        return ""
    texte = html.unescape(valeur)
    for invisible in ("\xa0", "\n", "\r", "\t"):
        texte = texte.replace(invisible, " ")
    return " ".join(texte.split()).strip()


def normaliser_prix(valeur: object) -> str:
    """Prix brut → numérique « point-décimal », ou ``""`` si aucun nombre.

    « 1 752,90 € » → « 1752.90 » · « 19,36 € » → « 19.36 » · « Sur devis » → « ».
    """
    if not isinstance(valeur, str):
        return ""
    correspondance = _PRIX_RE.search(valeur)
    if not correspondance:
        return ""
    return re.sub(r"\s", "", correspondance.group(0)).replace(",", ".")
