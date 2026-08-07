"""
Clé de page par fournisseur — ce qui identifie une fiche indépendamment du slug.

Partagé par ``audit_catalogue.py`` (comparer base et catalogue) et
``verifier_retraits.py`` (distinguer un renommage d'un retrait).

**Le slug n'identifie rien.** Deux constats mesurés le 07/08/2026 :

  - Legallais publie au sitemap un slug DIFFÉRENT de celui de ses propres URL
    (« embouts-4-pans---standard » en base contre « embouts-4-pans-standard »).
    Une clé incluant le slug faisait tomber la correspondance à 51 % et annonçait
    12 791 gammes orphelines au lieu de 40.
  - Setin renomme ses fiches en place et redirige l'ancienne URL vers la
    nouvelle : ``…-extracteur-reglable-unior-682/2-a5676.html`` redirige vers
    ``…-extracteur-a-griffe-reglable-unior-682/2-unior-tools-a5676.html``.
    **Même id ``a5676``** — l'article est vivant, seul son libellé a changé.

D'où : la clé est l'identifiant **numérique** ou **technique** de la page, jamais
son libellé.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from core.dedup import normaliser_url

#: Setin : l'identifiant de fiche est le jeton « aNNNNN » avant « .html »
#: (« cNNNN » désigne une CATÉGORIE — utile pour repérer une redirection de repli).
_RE_SETIN_FICHE = re.compile(r"-a(\d+)\.html?$", re.I)
_RE_SETIN_CATEGORIE = re.compile(r"-c(\d+)\.html?$", re.I)


def _sans_query(url: str) -> str:
    """URL normalisée, paramètres retirés (Setin : ``?idvar=`` désigne la variante)."""
    morceaux = urlsplit(normaliser_url(url))
    return urlunsplit((morceaux.scheme, morceaux.netloc, morceaux.path, "", ""))


def _id_numerique(url: str) -> str:
    """Premier segment **numérique** du chemin — clé Legallais (id de gamme)."""
    for segment in urlsplit(normaliser_url(url)).path.split("/"):
        if segment.isdigit():
            return segment
    return ""


def _id_setin(url: str) -> str:
    """Identifiant de fiche Setin (« a7363 »), ou l'URL sans query en repli."""
    chemin = urlsplit(_sans_query(url)).path
    trouve = _RE_SETIN_FICHE.search(chemin)
    return f"a{trouve.group(1)}" if trouve else _sans_query(url)


def est_categorie_setin(url: str) -> bool:
    """Vrai si l'URL désigne une catégorie Setin et non une fiche.

    Une fiche retirée peut répondre 200 après redirection vers sa rubrique : le
    statut HTTP ne suffit alors pas à conclure, la CIBLE de la redirection si.
    """
    return bool(_RE_SETIN_CATEGORIE.search(urlsplit(url or "").path))


#: Comment ramener l'URL d'un fournisseur à sa page identifiante.
CLES: dict[str, object] = {
    "prolians": normaliser_url,   # une page = un article, l'URL suffit
    "sider": normaliser_url,      # le fragment #ref est retiré par la normalisation
    "setin": _id_setin,           # id « aNNNNN » : le slug bouge, pas lui
    "legallais": _id_numerique,   # id de gamme
    "sonepar": None,              # aucune énumération disponible
}


def cle_page(site: str, url: str) -> str:
    """Clé de page du fournisseur, ou ``""`` si l'URL n'en porte pas."""
    fonction = CLES.get(site)
    if fonction is None:
        return ""
    return fonction(url)
