"""
Registre des **méthodes** de scraping — la voie est un paramètre, pas un type.

Avant : chaque variante de scrape produits réclamait son propre script et son
propre bouton dans la GUI (``produits``, ``catalogue_complet``,
``catalogue_light_full``, ``maj_prixstock``…). Cinq voies × cinq fournisseurs,
et l'écran s'allonge à chaque ajout — beaucoup de voies finissant codées mais
injoignables depuis l'interface.

Ici, un couple ``(type, fournisseur)`` porte plusieurs **méthodes** nommées, et
la GUI n'affiche qu'un sélecteur. Une méthode inconnue lève ``MethodeInconnue``
avant toute instanciation.

Le vocabulaire est **partagé entre fournisseurs** : « sitemap » veut dire la même
chose partout (énumérer par le sitemap XML publié), « api » aussi (interroger
l'API interne batchée). Chaque nom décrit une **voie d'énumération**, pas une
implémentation.

⚠️ Ces méthodes traitent tout comme des **articles simples** : aucune colonne de
déclinaison (parent / enfant / index) n'est produite. Les voies historiques par
catégories, elles, continuent de les renseigner — et ``save_product`` ne les
écrase pas, puisqu'un champ absent n'efface jamais une valeur en base.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, fields

from core.sink import SinkMariaDB

_log = logging.getLogger(__name__)

# Vocabulaire des voies d'énumération.
METHODE_SITEMAP = "sitemap"        # sitemap XML publié, sans login
METHODE_API = "api"                # API interne batchée (GraphQL Prolians)
METHODE_CATEGORIES = "categories"  # arbre de catégories, listes paginées
METHODE_REFERENCE = "reference"    # recherche par référence (exige une liste)


class MethodeInconnue(Exception):
    """La méthode demandée n'existe pas pour ce couple (type, fournisseur)."""


class ParametresInvalides(Exception):
    """Les paramètres ne correspondent pas au modèle de la méthode."""


@dataclass(frozen=True)
class Methode:
    """Une voie exécutable : classe du scraper + dataclass de ses paramètres."""

    fabrique: Callable
    params: Callable
    libelle: str
    #: La méthode a-t-elle besoin d'une session fournisseur pour donner les prix ?
    besoin_session: bool = True


@dataclass(frozen=True)
class Voie:
    """Les méthodes disponibles pour un couple ``(type, fournisseur)``.

    ``defaut`` est la voie appliquée quand aucune n'est précisée — donc la voie
    de production recommandée pour ce fournisseur.
    """

    methodes: dict[str, Methode]
    defaut: str

    def __post_init__(self) -> None:
        if self.defaut not in self.methodes:  # garde de programmation
            raise ValueError(
                f"Méthode par défaut « {self.defaut} » absente de {list(self.methodes)}"
            )


def _voie(defaut: str, **methodes: Methode) -> Voie:
    """Raccourci de déclaration : ``_voie("sitemap", sitemap=…, api=…)``."""
    return Voie(methodes=dict(methodes), defaut=defaut)


def _registre() -> dict[tuple[str, str], Voie]:
    """Construit le registre. Imports **tardifs** : charger ce module ne doit pas
    tirer Playwright ni BeautifulSoup tant qu'aucune méthode n'est demandée."""
    from scrapers.Legallais_P1.products.methode_sitemap import (
        LegallaisSitemap, ParamsLegallaisSitemap,
    )
    from scrapers.Prolians_P3.products.methode_api import ParamsProliansApi, ProliansApi
    from scrapers.Setin_P5.products.methode_sitemap import ParamsSetinSitemap, SetinSitemap

    return {
        # Prolians (P3) — l'API GraphQL rend la fiche RICHE (description, images,
        # attributs, éco-part) en plus des prix, sur le même appel batché par 100.
        # La voie DOM (une page rendue par fiche) n'a plus de raison d'être.
        ("produits", "prolians"): _voie(
            METHODE_API,
            api=Methode(ProliansApi, ParamsProliansApi,
                        "API GraphQL — catalogue enrichi (prix, description, images)"),
        ),
        # Setin (P5) — sitemap + JSON inline : prix numérique, quantité de stock,
        # EAN et réf fabricant, sans ouvrir une page par fiche.
        ("produits", "setin"): _voie(
            METHODE_SITEMAP,
            sitemap=Methode(SetinSitemap, ParamsSetinSitemap,
                            "Sitemap + JSON inline — catalogue complet (~20 000 fiches)"),
        ),
        # Legallais (P1) — passe d'ENRICHISSEMENT : le sitemap rate les fiches
        # « gamme » (tableau chargé en JS). À combiner avec la voie catégories.
        ("produits", "legallais"): _voie(
            METHODE_SITEMAP,
            sitemap=Methode(LegallaisSitemap, ParamsLegallaisSitemap,
                            "Sitemap + prix article — passe d'enrichissement"),
        ),
    }


_CACHE: dict[tuple[str, str], Voie] | None = None


def registre() -> dict[tuple[str, str], Voie]:
    """Le registre, construit une seule fois."""
    global _CACHE
    if _CACHE is None:
        _CACHE = _registre()
    return _CACHE


def voie(type_scrap: str, fournisseur: str) -> Voie | None:
    """La voie d'un couple, ou ``None`` s'il n'a aucune méthode enregistrée."""
    return registre().get((type_scrap, fournisseur))


def methodes_disponibles(type_scrap: str, fournisseur: str) -> list[str]:
    """Noms des méthodes, **défaut en tête** (la GUI peut le proposer d'emblée)."""
    trouvee = voie(type_scrap, fournisseur)
    if trouvee is None:
        return []
    autres = sorted(m for m in trouvee.methodes if m != trouvee.defaut)
    return [trouvee.defaut, *autres]


def libelle(type_scrap: str, fournisseur: str, methode: str) -> str:
    """Libellé lisible d'une méthode, ou son nom brut si elle est inconnue."""
    trouvee = voie(type_scrap, fournisseur)
    if trouvee is None or methode not in trouvee.methodes:
        return methode
    return trouvee.methodes[methode].libelle


def resoudre(type_scrap: str, fournisseur: str, methode: str | None = None,
             parametres: dict | None = None) -> tuple[Methode, object]:
    """Résout une méthode et valide ses paramètres.

    Returns:
        ``(methode, parametres)`` — les paramètres étant une instance de la
        dataclass de la méthode.

    Raises:
        MethodeInconnue: couple sans méthode, ou méthode absente du couple.
        ParametresInvalides: paramètre inconnu de la dataclass.
    """
    trouvee = voie(type_scrap, fournisseur)
    if trouvee is None:
        raise MethodeInconnue(f"({type_scrap}, {fournisseur}) n'a aucune méthode enregistrée")
    nom = methode or trouvee.defaut
    entree = trouvee.methodes.get(nom)
    if entree is None:
        raise MethodeInconnue(
            f"méthode « {nom} » inconnue pour ({type_scrap}, {fournisseur}) — "
            f"disponibles : {', '.join(sorted(trouvee.methodes))}"
        )
    connus = {f.name for f in fields(entree.params)}
    fournis = dict(parametres or {})
    inconnus = set(fournis) - connus
    if inconnus:
        raise ParametresInvalides(
            f"paramètre(s) inconnu(s) pour « {nom} » : {', '.join(sorted(inconnus))} — "
            f"attendus : {', '.join(sorted(connus))}"
        )
    return entree, entree.params(**fournis)


def lancer(site: str, type_scrap: str = "produits", methode: str | None = None,
           parametres: dict | None = None, *,
           emettre_progres: Callable[[dict], None] | None = None,
           doit_annuler: Callable[[], bool] | None = None,
           reprise: dict | None = None) -> dict:
    """Exécute une méthode de bout en bout et retourne son bilan. **Bloquant**.

    Crée le sink MariaDB, instancie le scraper et déroule sa boucle asyncio dans
    le thread courant — la GUI l'appelle donc depuis son thread de travail, pas
    depuis la boucle Tkinter.

    Returns:
        Le résultat du scraper fusionné avec le bilan d'écriture (nouvelles
        fiches, fiches enrichies, inchangées, erreurs).
    """
    entree, params = resoudre(type_scrap, site, methode, parametres)
    sink = SinkMariaDB(site)
    scraper = entree.fabrique(
        params,
        emettre_progres=emettre_progres,
        doit_annuler=doit_annuler,
        sink=sink,
        reprise=reprise,
    )
    _log.info("Méthode %s/%s : %s", site, methode or "défaut", entree.libelle)
    resultat = asyncio.run(scraper.run())
    return {**(resultat or {}), **sink.bilan()}
