"""
Contrat commun des scrapers « méthode » — porté de SCRAPPER_App.

Un scraper de ce type ne connaît ni la base, ni la GUI : il reçoit à la
construction de quoi **remonter sa progression**, **savoir s'il doit s'arrêter**
et **émettre ses données**. C'est ce découplage qui permet de le lancer depuis
l'interface Tkinter aussi bien que depuis un script.

Deux classes :
  - ``Scraper``          — le contrat (progression, annulation, émission, reprise)
  - ``PlaywrightScraper`` — le moteur par défaut (Chromium visible + session)

Différence avec ``core/base_scraper.py`` : celui-ci est le socle des scrapers
historiques (un scraper = un script, sa propre boucle, sa propre écriture DB).
``Scraper`` est le socle des **méthodes** enfilables : plusieurs voies possibles
pour un même couple (type, fournisseur), toutes écrivant par le même sink.

Pas de gestion des déclinaisons : chaque fiche émise est un **article simple**
(décision 06/08/2026). Les colonnes ``product_combination_*`` /
``product_parent_reference`` / ``product_child_reference`` ne sont jamais
renseignées par ces méthodes — et ``save_product`` ne les écrase pas si une
autre voie les a déjà remplies.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

# Clé réservée dans la charge de progression : le point de reprise durable.
CLE_REPRISE = "reprise"

# Cadence du battement de progression (secondes).
INTERVALLE_BATTEMENT = 30.0


class ScrapeAnnule(Exception):
    """Levée par un scraper qui s'arrête proprement sur demande d'annulation."""


class Scraper(abc.ABC):
    """Contrat d'une méthode de scraping exécutable."""

    #: Code fournisseur écrit dans ``product_fournisseur`` (« P1 », « P3 »…).
    FOURNISSEUR: str = ""
    #: Clé du dossier de session sous ``playwright_profiles/``.
    FOURNISSEUR_SESSION: str = ""

    def __init__(
        self,
        parametres: Any = None,
        *,
        emettre_progres: Callable[[dict], None] | None = None,
        doit_annuler: Callable[[], bool] | None = None,
        sink: Any = None,
        reprise: dict | None = None,
    ) -> None:
        self.parametres = parametres
        self._emettre = emettre_progres or (lambda _charge: None)
        self._doit_annuler = doit_annuler or (lambda: False)
        self._sink = sink
        # Point de reprise publié par une tentative précédente ; ``{}`` = run complet.
        self.reprise: dict = reprise or {}
        self._pages_vues = 0

    # ─── Progression ─────────────────────────────────────────────────────────

    def progres(self, progression: dict) -> None:
        """Remonte une progression (relayée à l'appelant : GUI, journal…)."""
        self._emettre(progression)

    def page_vue(self, n: int = 1) -> None:
        """Compte ``n`` page(s)/fiche(s) réellement parcourue(s).

        À appeler **au fil de l'eau**, pas aux frontières de lot : c'est ce qui
        rend l'avancement visible en continu. Un fetch échoué compte aussi —
        c'est du travail parcouru.
        """
        self._pages_vues += n

    @property
    def pages_vues(self) -> int:
        """Pages/fiches parcourues depuis le début du run."""
        return self._pages_vues

    def publier_reprise(self, etat: dict, progression: dict | None = None) -> None:
        """Publie un point de reprise durable, avec une progression optionnelle.

        ``etat`` est **opaque** : un contrat du scraper avec lui-même à travers
        le temps. Il doit identifier la dernière unité traitée **par valeur**
        (URL, catégorie…) et jamais par offset — un offset devient faux dès que
        le catalogue bouge entre deux tentatives.

        À appeler à la granularité du **lot**, pas de l'élément : au pire un lot
        est rejoué après un crash, et l'écriture étant idempotente (product_uid),
        ce rejeu est sans effet.
        """
        charge = dict(progression or {})
        charge[CLE_REPRISE] = etat
        self._emettre(charge)

    @contextlib.asynccontextmanager
    async def battement(
        self, etat: Callable[[], dict], *, intervalle: float = INTERVALLE_BATTEMENT
    ) -> AsyncIterator[None]:
        """Publie ``etat()`` périodiquement tant que le bloc s'exécute.

        Un scraper qui ne parle qu'aux frontières de lot devient muet pendant
        tout un lot — et un lot de 200 fiches dépasse les 10 minutes dès que le
        site ralentit. Le battement découple la **visibilité** du **rythme du
        travail**.

        Ne publie **jamais** de point de reprise : à l'intérieur d'un lot les
        unités se terminent dans le désordre (``gather``), donc « la dernière
        faite » n'a pas de sens. Le checkpoint reste aux frontières de lot.
        """

        async def _boucle() -> None:
            while True:
                await asyncio.sleep(intervalle)
                with contextlib.suppress(Exception):
                    self.progres(etat())

        tache = asyncio.create_task(_boucle())
        try:
            yield
        finally:
            tache.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tache

    # ─── Émission des données ────────────────────────────────────────────────

    def emettre_donnee(self, cible: str, element: dict) -> None:
        """Pousse un enregistrement vers le sink (``produits``, ``suivis``…).

        Sans sink (tests), c'est un no-op — ce qui permet d'exercer un scraper
        complet sans base.
        """
        if self._sink is not None:
            self._sink.ajouter(cible, element)

    # ─── Annulation coopérative ──────────────────────────────────────────────

    def should_stop(self) -> bool:
        """Vrai si une annulation a été demandée."""
        return self._doit_annuler()

    def verifier_annulation(self) -> None:
        """Lève ``ScrapeAnnule`` si l'arrêt a été demandé."""
        if self.should_stop():
            raise ScrapeAnnule("Arrêt demandé")

    @abc.abstractmethod
    async def run(self) -> dict:
        """Exécute le scrape et retourne un dict de compteurs."""


class PlaywrightScraper(Scraper):
    """Moteur par défaut : Chromium **visible**, session par ``storage_state``.

    Le navigateur ne sert souvent qu'à **porter la session** : les méthodes
    « légères » passent ensuite par ``contexte.request`` (APIRequestContext),
    qui rejoue les cookies sans ouvrir de page.
    """

    def __init__(self, parametres: Any = None, **kw: Any) -> None:
        super().__init__(parametres, **kw)
        self._playwright: Playwright | None = None
        self._navigateur: Browser | None = None
        self._contexte: BrowserContext | None = None
        self._chemin_session: Path | None = None

    @property
    def contexte(self) -> BrowserContext:
        if self._contexte is None:
            raise RuntimeError("Navigateur non démarré : appeler demarrer_navigateur().")
        return self._contexte

    async def demarrer_navigateur(
        self, *, storage_state: str | Path | None = None, headless: bool = False
    ) -> None:
        """Lance Chromium et amorce la session si le fichier existe.

        ``storage_state`` est passé en **lecture** à ``new_context`` : plusieurs
        runs du même fournisseur peuvent donc partager la même session. Le
        chemin est mémorisé pour ``persister_session`` en fin de run.
        """
        from playwright.async_api import async_playwright

        self._chemin_session = Path(storage_state) if storage_state else None
        self._playwright = await async_playwright().start()
        self._navigateur = await self._playwright.chromium.launch(headless=headless)
        args: dict[str, Any] = {}
        if self._chemin_session and self._chemin_session.exists():
            args["storage_state"] = str(self._chemin_session)
        self._contexte = await self._navigateur.new_context(**args)

    async def nouvelle_page(self) -> Page:
        return await self.contexte.new_page()

    async def sauver_session(self, chemin: str | Path) -> None:
        """Persiste le ``storage_state`` (cookies + localStorage)."""
        if self._contexte is None:
            return
        etat = await self._contexte.storage_state()
        chemin = Path(chemin)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(etat, ensure_ascii=False, indent=2), encoding="utf-8")

    async def persister_session(self) -> None:
        """Réécrit la session amorcée avec les cookies rafraîchis du run.

        Sans ça, un run de plusieurs heures laisse sur le disque des cookies
        périmés alors que le site en a servi des frais tout du long : le run
        suivant repart d'une session déjà morte.
        """
        if self._chemin_session is not None:
            with contextlib.suppress(Exception):
                await self.sauver_session(self._chemin_session)

    async def fermer(self) -> None:
        """Ferme contexte, navigateur et Playwright — sans jamais lever."""
        for fermeture in (
            getattr(self._contexte, "close", None),
            getattr(self._navigateur, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if fermeture is not None:
                with contextlib.suppress(Exception):
                    await fermeture()
        self._contexte = None
        self._navigateur = None
        self._playwright = None
