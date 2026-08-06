"""
Méthode « sitemap » — catalogue Legallais par sitemap + HTTP concurrent (P1).

Voie rapide face au parcours par référence (une fiche à la fois, rendu Chrome) :

1. **Énumération** — le sitemap liste ~48 000 fiches en HTTP simple, sans antibot
   ni login (``legallais_sitemap.iter_entrees_produit``).
2. **Fetch concurrent poli** — chaque fiche est récupérée en **HTTP** via
   l'``APIRequestContext`` Playwright (session rejouée), **sans ouvrir de page** :
   jitter + back-off, pour ne pas déclencher de blocage IP sur le CDN.
3. **Parse statique** — ``legallais_fiche_html.parser_fiche`` lit désignation,
   marque, images, description, caractéristiques, EAN, docs, fil d'Ariane.
4. **Enrichissement prix** — la fiche récupérée **authentifiée** rend la table des
   références côté serveur : ``articles_codes`` en extrait les codes article, et
   ``/get-article-infos/<code>`` donne le **prix net compte**. On émet alors
   **une ligne par article** (l'unité vendable).

⚠️ **Cette voie ne remplace pas le parcours par catégories.** Les fiches
« gamme » chargent leur tableau en JS et ne rendent aucun code en statique : elles
ne produisent que leur base page (sans prix). C'est une passe d'enrichissement
rapide, à combiner avec la voie historique.

⚠️ **Dualité nologin / logué.** Le catalogue diffère selon l'état de connexion :
en **logué** on a les prix mais certaines fiches disparaissent (404) ; en
**nologin** pas de prix, mais **plus de fiches** (les articles indisponibles
gardent leur page publiée). D'où ``utiliser_session`` : passe 1 à ``False``
(+ ``enrichir_prix=False``) pour capturer un maximum de fiches, passe 2 à ``True``
pour enrichir les prix. La passe nologin **n'écrase jamais** la session stockée.

⚠️ **Pas d'auto-login** : Legallais est protégé par un captcha proof-of-work et
son login passe par Botasaurus. Sans session valide, cette méthode émet des
fiches publiques sans prix. Se connecter d'abord via
``auth/legallais/manual_login_legallais.py``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from core.f2 import element_produit
from core.garde_session import message_progression
from core.http_poli import entetes_navigateur, get_poli
from core.reprise import enumerer_apres
from core.scrap_base import PlaywrightScraper, ScrapeAnnule
from core.sessions import GestionnaireSessions

from . import legallais_sitemap
from .legallais_article_infos import mapper_article, recuperer_article
from .legallais_fiche_html import articles_codes, parser_fiche

_log = logging.getLogger(__name__)

FOURNISSEUR = "P1"
FOURNISSEUR_SESSION = "legallais"

#: Entrées sitemap accumulées avant un round de fetch concurrent + émission.
TAILLE_LOT = 200

#: Borne du chargement du sitemap. Normalement quelques secondes ; borné haut car
#: ``urllib`` peut staller en trickle sous throttling antibot.
TIMEOUT_SITEMAP_S = 300.0


@dataclass
class ParamsLegallaisSitemap:
    """Paramètres bornés de la méthode « sitemap »."""

    #: Limite de **fiches** traitées ; ``None`` = tout le catalogue.
    limit: int | None = None
    #: Requêtes HTTP simultanées. Borné bas : rester poli face au CDN antibot.
    concurrence: int = 6
    #: Enrichir chaque fiche par les prix article. ``False`` = base page seule.
    enrichir_prix: bool = True
    #: Amorcer la session stockée. ``False`` = passe « fiches » non loguée.
    utiliser_session: bool = True


def storage_state_a_amorcer(utiliser_session: bool, session_existe: bool,
                            chemin: Path) -> Path | None:
    """Quel ``storage_state`` amorcer selon le mode de passe. **Pur**.

    Passe **nologin** : la session stockée est **toujours ignorée**, même
    présente — c'est le but de la passe 1. Passe **loguée** : session amorcée si
    elle existe, sinon ``None`` (fiches publiques).
    """
    if not utiliser_session:
        return None
    return chemin if session_existe else None


def taille_a_traiter(taille_lot: int, faites: int, limit: int | None) -> int:
    """Combien d'entrées du lot traiter sans dépasser ``limit`` (en fiches). **Pur**."""
    if limit is None:
        return taille_lot
    return max(0, min(taille_lot, limit - faites))


class LegallaisSitemap(PlaywrightScraper):
    """Énumère le catalogue Legallais (sitemap) et pousse des fiches enrichies."""

    FOURNISSEUR = FOURNISSEUR
    FOURNISSEUR_SESSION = FOURNISSEUR_SESSION
    CIBLE = "produits"

    def __init__(self, parametres: ParamsLegallaisSitemap | None = None, **kw) -> None:
        super().__init__(parametres or ParamsLegallaisSitemap(), **kw)

    async def _demarrer_avec_session(self) -> None:
        """Amorce le navigateur avec (ou sans) la session, selon le mode de passe."""
        chemin = GestionnaireSessions().chemin_session(FOURNISSEUR_SESSION)
        storage_state = storage_state_a_amorcer(
            self.parametres.utiliser_session, chemin.exists(), chemin
        )
        if storage_state is None and self.parametres.utiliser_session:
            _log.warning(
                "Session Legallais absente — fiches publiques, sans prix compte. "
                "Se connecter via auth/legallais/manual_login_legallais.py."
            )
        elif storage_state is None:
            _log.info("Passe nologin : session ignorée (catalogue public, sans prix).")
        await self.demarrer_navigateur(storage_state=storage_state)

    async def run(self) -> dict:
        params: ParamsLegallaisSitemap = self.parametres
        await self._demarrer_avec_session()
        entetes = entetes_navigateur()

        emis = 0    # lignes écrites (articles ou fiches)
        fiches = 0  # fiches traitées → pilote ``limit``
        lot: list[dict] = []
        depuis = self.reprise.get("derniere_url")
        if depuis:
            _log.info("Reprise après %s : les fiches précédentes sont sautées.", depuis)

        try:
            async with self.battement(
                lambda: {
                    "message": message_progression(self.pages_vues, emis),
                    "pages": self.pages_vues,
                    "produits": emis,
                }
            ):
                # ⚠️ Sitemap chargé en THREAD : ``iter_entrees_produit`` fait des GET
                # urllib **synchrones** au fil de l'itération. Les laisser dans la
                # boucle async la gèle (battement ET annulation compris) à chaque
                # feuille — un run a déjà été figé 13 h sur une feuille stallée.
                entrees = await asyncio.wait_for(
                    asyncio.to_thread(self._charger_entrees, depuis),
                    timeout=TIMEOUT_SITEMAP_S,
                )
                _log.info("Sitemap Legallais chargé : %d fiches à traiter.", len(entrees))

                for entree in entrees:
                    self.verifier_annulation()
                    lot.append(entree)
                    if len(lot) >= TAILLE_LOT:
                        n_fiches, n_emis = await self._traiter_lot(lot, entetes, fiches)
                        fiches += n_fiches
                        emis += n_emis
                        if n_fiches:
                            # ⚠️ ``lot[n_fiches - 1]`` et NON ``lot[-1]`` :
                            # ``_traiter_lot`` tronque le lot au reste autorisé par
                            # ``limit``, donc la dernière fiche ACCUMULÉE n'est pas la
                            # dernière fiche TRAITÉE. Publier lot[-1] sur-déclarerait
                            # l'avancement et ferait sauter, à la reprise, des fiches
                            # jamais scrapées.
                            self.publier_reprise(
                                {"derniere_url": lot[n_fiches - 1]["url"], "fiches": fiches},
                                {"message": message_progression(fiches, emis)},
                            )
                        lot = []
                        if params.limit and fiches >= params.limit:
                            return self._resultat(emis)
                    await asyncio.sleep(0)  # cède la main (annulation réactive)
                if lot:
                    _, n_emis = await self._traiter_lot(lot, entetes, fiches)
                    emis += n_emis
        except ScrapeAnnule:
            _log.info("Scrape Legallais interrompu à la demande — %d ligne(s).", emis)
        finally:
            # Réécrit les cookies rafraîchis si le run a produit des données. En passe
            # nologin c'est un no-op garanti (aucune session amorcée) → la session
            # loguée stockée n'est JAMAIS écrasée par un état anonyme.
            if emis > 0:
                await self.persister_session()
            await self.fermer()

        self.progres({"message": message_progression(fiches, emis)})
        return self._resultat(emis)

    # ─── Étapes ──────────────────────────────────────────────────────────────

    def _charger_entrees(self, depuis: str | None) -> list[dict]:
        """Matérialise la liste des fiches du sitemap (après reprise). **Bloquant**."""
        return list(
            enumerer_apres(
                legallais_sitemap.iter_entrees_produit, depuis, lambda e: e["url"]
            )
        )

    async def _traiter_lot(self, lot: list[dict], entetes: dict[str, str],
                           fiches: int) -> tuple[int, int]:
        """Fetch concurrent + parse + enrichissement + émission. ``(fiches, émis)``."""
        a_traiter = lot[:taille_a_traiter(len(lot), fiches, self.parametres.limit)]
        if not a_traiter:
            return (0, 0)
        semaphore = asyncio.Semaphore(self.parametres.concurrence)
        listes = await asyncio.gather(
            *(self._traiter_fiche(entree, entetes, semaphore) for entree in a_traiter)
        )
        emis = 0
        for lignes in listes:
            for ligne in lignes:
                self.emettre_donnee(self.CIBLE, ligne)
                emis += 1
        return (len(a_traiter), emis)

    async def _traiter_fiche(self, entree: dict, entetes: dict[str, str],
                             semaphore: asyncio.Semaphore) -> list[dict]:
        """Une fiche → lignes produit (une par article enrichi, sinon la base page).

        Repli sur la base page si l'enrichissement est désactivé, si la fiche
        n'expose aucun code, ou si tous les appels prix échouent : la fiche n'est
        jamais perdue.
        """
        url = entree["url"]
        request = self._contexte.request
        async with semaphore:
            html = await get_poli(request, url, entetes=entetes)
        # Compteur vivant : la fiche est parcourue dès le fetch (succès ou non),
        # AVANT l'enrichissement — c'est ce qui fait avancer l'affichage pendant le lot.
        self.page_vue()
        if not html:
            _log.debug("Fiche non récupérée : %s", url)
            return []

        base = parser_fiche(html, url)
        codes = articles_codes(html) if self.parametres.enrichir_prix else []
        if not codes:
            return [element_produit(base, FOURNISSEUR)]

        lignes: list[dict] = []
        for code in codes:
            async with semaphore:
                result = await recuperer_article(request, code, entetes=entetes)
            if result:
                lignes.append(element_produit(mapper_article(result, base), FOURNISSEUR))
        return lignes or [element_produit(base, FOURNISSEUR)]

    @staticmethod
    def _resultat(emis: int) -> dict:
        return {"produits": emis}
