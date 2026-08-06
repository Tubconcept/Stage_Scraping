"""
Sink d'écriture des méthodes de scraping → MariaDB.

Remplace l'outbox de SCRAPPER_App : là-bas les éléments partaient vers un
service HTTP, ici ils vont directement en base. Le contrat est le même côté
scraper (``emettre_donnee(cible, element)``), donc les scrapers portés ne
changent pas d'une ligne.

Pourquoi ça reste sûr sans outbox : ``db.mariadb_db.save_product`` est
**idempotent** (identité ``product_uid``, cf. ``core/dedup.py``). Rejouer un lot
après un crash n'ajoute aucun doublon — c'est exactement la propriété que
l'ingestion serveur garantissait.

Écriture **non destructive** : un champ absent de l'élément n'écrase jamais la
valeur en base. Une méthode « légère » (sitemap) peut donc enrichir une fiche
déjà collectée par une voie « catégories » sans lui faire perdre ses
déclinaisons ou sa description.
"""

from __future__ import annotations

import logging
from collections import Counter

from db.mariadb_db import insert_tracking, save_product

_log = logging.getLogger(__name__)

#: Cibles acceptées par ``ajouter``.
CIBLE_PRODUITS = "produits"
CIBLE_SUIVIS = "suivis_colis"


class SinkMariaDB:
    """Accumule les compteurs et écrit chaque élément dans la table du site.

    Args:
        site: clé fournisseur (« legallais », « prolians », « setin »…).
        journal: logger optionnel (celui du scraper, pour tracer au bon endroit).
    """

    def __init__(self, site: str, journal: logging.Logger | None = None) -> None:
        self.site = site
        self._log = journal or _log
        self.compteurs: Counter[str] = Counter()

    def ajouter(self, cible: str, element: dict) -> None:
        """Écrit un élément. N'interrompt jamais le scrape sur une erreur unitaire.

        Une fiche qui échoue à l'écriture est comptée dans ``erreurs`` et
        journalisée : perdre une fiche est regrettable, perdre le run entier
        (des heures de scrape) parce qu'une ligne est mal formée ne l'est pas.
        """
        try:
            if cible == CIBLE_PRODUITS:
                resultat = save_product(None, self.site, element)
                self.compteurs[resultat] += 1
            elif cible == CIBLE_SUIVIS:
                insert_tracking(None, self.site, element)
                self.compteurs["suivi"] += 1
            else:
                raise ValueError(f"Cible d'ingestion inconnue : {cible!r}")
            self.compteurs[cible] += 1
        except Exception as exc:
            self.compteurs["erreurs"] += 1
            self._log.warning(
                "Écriture %s ignorée (%s) : %s", cible, exc,
                element.get("product_fournisseur_url", "?"),
            )

    def bilan(self) -> dict:
        """Compteurs du run : nouvelles fiches, fiches enrichies, inchangées, erreurs."""
        return {
            "produits": self.compteurs[CIBLE_PRODUITS],
            "nouveaux": self.compteurs["insert"],
            "enrichis": self.compteurs["update"],
            "inchanges": self.compteurs["inchange"],
            "erreurs": self.compteurs["erreurs"],
        }


class SinkMemoire:
    """Sink de test : garde tout en mémoire, n'écrit nulle part."""

    def __init__(self) -> None:
        self.elements: dict[str, list[dict]] = {}

    def ajouter(self, cible: str, element: dict) -> None:
        self.elements.setdefault(cible, []).append(element)

    def bilan(self) -> dict:
        return {cible: len(lignes) for cible, lignes in self.elements.items()}
