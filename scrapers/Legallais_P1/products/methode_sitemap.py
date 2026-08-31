"""
Méthode « sitemap » — catalogue Legallais par sitemap + HTTP concurrent (P1).

Voie rapide face au parcours par référence (une fiche à la fois, rendu Chrome) :

1. **Énumération** — le sitemap liste ~48 000 fiches en HTTP simple, sans antibot
   ni login (``legallais_sitemap.iter_entrees_produit``).
2. **Fetch concurrent poli** — chaque fiche est récupérée en **HTTP** via
   l'``APIRequestContext`` Playwright (session rejouée), **sans ouvrir de page** :
   jitter + back-off, pour ne pas déclencher de blocage IP sur le CDN.
3. **Parse statique** — ``legallais_fiche_html.fiche_et_articles`` lit la base
   page (désignation, description, docs, fil d'Ariane) **et le JSON inline des
   articles** : un extrait par déclinaison, chacun avec sa référence, sa
   **référence fabricant** (``codeProvider``), ses axes et son état.
4. **Enrichissement prix** — ``/get-article-infos/<code>`` donne le **prix net
   compte** de chaque article déjà identifié. On émet **une ligne par article**
   (l'unité vendable), déclinaisons comprises.

⚠️ **Cette voie ne remplace pas le parcours par catégories.** Une part des fiches
ne publie aucun article (gamme dont la commercialisation est arrêtée) : elles ne
produisent que leur base page, sans prix. C'est une passe d'enrichissement
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
from .legallais_fiche_html import articles_codes, fiche_et_articles, url_article

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


def doit_enrichir_prix(enrichir: bool, session_amorcee: bool) -> bool:
    """Faut-il appeler l'endpoint prix ? **Pur**.

    ⚠️ **Jamais sans session.** ``/get-article-infos`` répond aussi en anonyme,
    mais son ``net_price`` vaut alors le prix **public** — qui écraserait en base
    le prix compte déjà collecté. Un prix manquant se rattrape à la passe
    suivante ; un tarif public pris pour un tarif négocié, non.
    """
    return enrichir and session_amorcee


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
        #: Une session a-t-elle réellement été amorcée ? Pilote l'accès au prix.
        self.session_amorcee = False

    async def _demarrer_avec_session(self) -> None:
        """Amorce le navigateur avec (ou sans) la session, selon le mode de passe."""
        chemin = GestionnaireSessions().chemin_session(FOURNISSEUR_SESSION)
        storage_state = storage_state_a_amorcer(
            self.parametres.utiliser_session, chemin.exists(), chemin
        )
        self.session_amorcee = storage_state is not None
        if storage_state is None and self.parametres.utiliser_session:
            _log.warning(
                "Session Legallais absente — fiches publiques, PRIX NON COLLECTÉS "
                "(le tarif servi en anonyme est le prix public, il écraserait le "
                "prix compte). Se connecter via auth/legallais/manual_login_legallais.py."
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
        """Une fiche → une ligne par **article**, sinon la base page.

        Les articles viennent du JSON inline de la fiche : c'est là, et là seule,
        que chaque déclinaison porte sa propre référence fabricant. L'appel prix
        ne fait ensuite que poser le prix compte sur un article déjà identifié.

        Repli sur la base page quand la fiche ne publie aucun article : la fiche
        n'est jamais perdue.
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

        base, articles = fiche_et_articles(html, url)
        if not articles:
            articles = self._repli_table(html, base, url)
        if not articles:
            return [element_produit(base, FOURNISSEUR)]
        if not doit_enrichir_prix(self.parametres.enrichir_prix, self.session_amorcee):
            return [element_produit(article, FOURNISSEUR) for article in articles]

        lignes: list[dict] = []
        for article in articles:
            async with semaphore:
                result = await recuperer_article(request, article["ref"], entetes=entetes)
            # Sans prix, l'article reste émis : son identité (réf, réf fabricant,
            # axes) est déjà complète, seul le prix manquera.
            enrichi = mapper_article(result, article) if result else article
            lignes.append(element_produit(enrichi, FOURNISSEUR))
        return lignes

    @staticmethod
    def _repli_table(html: str, base: dict, url: str) -> list[dict]:
        """Articles reconstruits depuis la table HTML quand le JSON manque.

        Garde-fou : si Legallais retirait l'attribut ``data-pages--product-articles-value``
        sans que la table bouge, on continuerait d'émettre un article par ligne —
        **sans référence fabricant** plutôt qu'avec celle de la page, qui n'est
        celle d'aucun article en particulier.
        """
        codes = articles_codes(html)
        if not codes:
            return []
        _log.warning(
            "Fiche sans JSON d'articles mais %d ligne(s) de table : %s "
            "— articles émis sans référence fabricant.", len(codes), url,
        )
        # EAN : celui de la page ne vaut que pour une gamme à article unique.
        ean = base.get("ean", "") if len(codes) == 1 else ""
        return [
            {**base, "url": url_article(url, code), "ref": code,
             "ref_fabricant": "", "ean": ean}
            for code in codes
        ]

    @staticmethod
    def _resultat(emis: int) -> dict:
        return {"produits": emis}
