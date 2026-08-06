"""
Garde-fous de **session perdue** pour les méthodes légères.

Plusieurs fournisseurs coupent la session en cours de run et servent alors la
page **déconnectée** — avec les **prix publics** à la place des prix compte.
Persister une telle fiche écraserait des prix d'achat par des prix catalogue, en
silence et sur tout le catalogue. Chaque méthode légère détecte donc l'état
déconnecté et **saute** la fiche.

Sauter est nécessaire mais pas suffisant : sans les trois garde-fous ci-dessous,
un run entier peut parcourir des dizaines de milliers de fiches pour zéro
produit sans que rien ne le signale (vécu sur Setin : 6 200 fiches, 0 produit).

1. **Re-login à chaud** — rejoue l'auto-login puis recharge les cookies **dans le
   contexte déjà ouvert** (``clear_cookies`` + ``add_cookies``). On ne recrée pas
   le contexte : des requêtes sont en vol et ``contexte.request`` partage le même
   pot de cookies, les remplacer suffit. Sérialisé par un verrou — sans lui,
   trois fiches déconnectées en parallèle lanceraient trois navigateurs de login
   sur le même compte — plus un **compteur de génération** pour qu'une tâche
   sache qu'une autre a déjà réparé.
2. **Falaise** — N déconnexions d'affilée (compteur remis à zéro par le moindre
   succès) interrompent le run avec un message explicite.
3. **Progression lisible** — les fiches sautées apparaissent dans le message,
   sinon un run mort ressemble à « 4258 fiches parcourues, 0 produits » : vrai,
   mais illisible.

⚠️ Legallais n'a pas d'auto-login (captcha) : ``rafraichir_session`` échoue
proprement et la falaise prend le relais — le run s'arrête au lieu de boucler.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from core.login_auto import login_auto
from core.sessions import GestionnaireSessions

_log = logging.getLogger(__name__)

#: Tentatives de re-login par run. Borné : si deux auto-logins d'affilée ne
#: rendent pas la session, le problème n'est pas la fraîcheur (identifiants
#: absents, compte bloqué, site en rade) et réessayer n'ajoute que du bruit.
MAX_RELOGINS = 2

#: Déconnexions d'affilée avant d'abandonner. Assez haut pour absorber quelques
#: pages anormales, assez bas pour ne pas brûler un catalogue entier.
SEUIL_FALAISE = 20


def message_progression(fiches: int, emis: int, deconnectees: int = 0) -> str:
    """Ligne de progression affichée par la GUI. **Pur**.

    Les fiches sautées **doivent** apparaître : sans elles, un run entier
    ressemble à « 4258 fiches parcourues, 0 produits » — vrai, mais illisible.
    """
    texte = f"{fiches} fiches parcourues, {emis} produits"
    if deconnectees:
        texte += f", {deconnectees} sautées (session perdue)"
    return texte


class GardeSession:
    """Mixin : re-login à chaud + détection de falaise, pour un scraper Playwright.

    La classe hôte doit définir ``FOURNISSEUR_SESSION`` et exposer un
    ``self._contexte`` Playwright ouvert (fourni par ``PlaywrightScraper``).
    """

    FOURNISSEUR_SESSION: str = ""
    MAX_RELOGINS: int = MAX_RELOGINS
    SEUIL_FALAISE: int = SEUIL_FALAISE

    def _init_garde_session(self) -> None:
        """À appeler dans le ``__init__`` de la classe hôte."""
        self._verrou_session = asyncio.Lock()
        self._generation_session = 0
        self._relogins = 0
        self._deco_consecutives = 0

    @property
    def generation_session(self) -> int:
        """Numéro de génération de la session courante (incrémenté à chaque réparation)."""
        return self._generation_session

    def succes_fiche(self) -> None:
        """Un succès referme la falaise."""
        self._deco_consecutives = 0

    def noter_deconnexion(self) -> bool:
        """Compte une fiche sautée et **abandonne le run** si la falaise est atteinte.

        Sans ça, une session morte fait parcourir tout le catalogue pour zéro
        produit : des heures perdues, du trafic inutile chez le fournisseur, et
        un run qui finit « terminé » avec 0 ligne — le pire des silences.
        """
        self._deco_consecutives += 1
        if self._deco_consecutives >= self.SEUIL_FALAISE:
            raise RuntimeError(
                f"Session {self.FOURNISSEUR_SESSION} perdue : {self._deco_consecutives} "
                f"fiches déconnectées d'affilée après {self._relogins} tentative(s) de "
                f"re-login. Run interrompu — relancer après un login manuel."
            )
        return True

    async def rafraichir_session(self, generation_vue: int) -> bool:
        """Rejoue l'auto-login et recharge les cookies dans le contexte vivant.

        Args:
            generation_vue: génération observée par l'appelant avant d'attendre le
                verrou. Si elle a changé entre-temps, une autre tâche a déjà réparé.

        Returns:
            ``True`` si la session est (re)devenue exploitable.
        """
        async with self._verrou_session:
            if self._generation_session != generation_vue:
                return True  # une autre tâche a réparé pendant l'attente
            if self._relogins >= self.MAX_RELOGINS:
                return False
            self._relogins += 1
            _log.warning("Session %s perdue — auto-login %d/%d.",
                         self.FOURNISSEUR_SESSION, self._relogins, self.MAX_RELOGINS)

            sessions = GestionnaireSessions()
            chemin = sessions.chemin_session(self.FOURNISSEUR_SESSION)
            try:
                ok = await login_auto(self.FOURNISSEUR_SESSION, chemin)
            except Exception as exc:  # un login qui casse ne doit pas tuer le run
                _log.warning("Auto-login %s en échec : %s", self.FOURNISSEUR_SESSION, exc)
                return False
            if not ok or not await self.recharger_cookies(chemin):
                return False

            # Horodater la méta, sinon un re-login réussi reste invisible et la
            # session fraîche est réputée expirée dès le prochain contrôle d'âge.
            sessions.marquer_connecte(self.FOURNISSEUR_SESSION)
            self._generation_session += 1
            _log.info("Session %s rétablie (génération %d).",
                      self.FOURNISSEUR_SESSION, self._generation_session)
            return True

    async def recharger_cookies(self, chemin: Path) -> bool:
        """Injecte les cookies fraîchement écrits dans le contexte **déjà ouvert**.

        Appelé **dans** le verrou et **avant** d'incrémenter la génération : tant
        que les cookies ne sont pas réellement posés, la session n'est pas réparée.
        """
        try:
            etat = json.loads(Path(chemin).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _log.warning("Session %s illisible après login : %s",
                         self.FOURNISSEUR_SESSION, exc)
            return False
        cookies = etat.get("cookies") or []
        if not cookies:
            _log.warning("Session %s sans cookie après login.", self.FOURNISSEUR_SESSION)
            return False
        await self._contexte.clear_cookies()
        await self._contexte.add_cookies(cookies)
        return True
