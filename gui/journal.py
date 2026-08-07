"""
Pont entre le journal des scrapers et l'interface graphique.

Les scrapers historiques ne remontent aucune progression : ils **journalisent**
(« [42] 10013933 (3 ligne(s)) », « Reprise — 80 897 URL(s) déjà scrappée(s) »…).
Ces lignes partaient en console et dans ``log/``, jamais à l'écran — d'où
l'impression qu'un scrape lancé depuis la GUI ne fait rien.

Ce module capte ces lignes sans toucher à un seul scraper : un handler posé sur
le logger **racine** le temps du run, et une file d'attente que l'interface vide
depuis son propre thread.

Pourquoi une file et pas un appel direct à Tkinter :
  - Tkinter n'est pas thread-safe et le scraper tourne dans un thread de travail ;
  - un scrape de catalogue produit des **milliers** de lignes — les pousser une
    par une dans la boucle d'événements la saturerait. La file absorbe les
    rafales, l'interface en consomme un paquet borné toutes les 250 ms.

La file est **bornée** : sous rafale on jette les lignes en trop et on les
compte, plutôt que de laisser la mémoire enfler. Le fichier de log, lui, reste
complet — c'est lui qui fait foi.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading

#: Taille de la file. Au-delà, les lignes sont comptées puis jetées.
TAILLE_FILE = 2000

#: Loggers tiers trop bavards pour l'écran (leurs lignes restent dans log/).
PREFIXES_IGNORES = ("asyncio", "urllib3", "websockets", "playwright", "PIL")


class HandlerFileAttente(logging.Handler):
    """Handler qui dépose les lignes formatées dans une file, sans jamais bloquer.

    Un handler qui bloque bloquerait le scraper : sur file pleine on incrémente
    ``perdues`` et on continue.
    """

    def __init__(self, file: queue.Queue, niveau: int = logging.INFO) -> None:
        super().__init__(level=niveau)
        self._file = file
        self.perdues = 0
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(PREFIXES_IGNORES):
            return
        try:
            ligne = self.format(record)
        except Exception:  # un record mal formé ne doit pas tuer le scraper
            return
        if record.levelno >= logging.WARNING:
            ligne = f"[{record.levelname}] {ligne}"
        try:
            self._file.put_nowait(ligne)
        except queue.Full:
            self.perdues += 1


class FluxVersFile:
    """Remplace ``sys.stdout`` : recopie ce qui y est écrit dans la file.

    ⚠️ **Indispensable** : les scrapers historiques et les gestionnaires de
    session communiquent par ``print()`` — 144 appels dans ``auth/`` et
    ``scrapers/``, dont tout le déroulé de connexion (« Session Prolians
    chargée », « Login Prolians OK »). Lancés depuis la GUI, ces messages
    n'allaient nulle part. Les convertir en logs demanderait de toucher 144
    endroits ; les intercepter ici n'en demande qu'un.

    Le flux d'origine reste alimenté : en ligne de commande, rien ne change.
    """

    def __init__(self, file: queue.Queue, flux_origine) -> None:
        self._file = file
        self._origine = flux_origine
        self._tampon = ""
        self._verrou = threading.Lock()

    def write(self, texte: str) -> int:
        try:
            self._origine.write(texte)
        except Exception:  # console absente (pythonw) : on continue vers la file
            pass
        with self._verrou:
            self._tampon += texte
            *lignes, self._tampon = self._tampon.split("\n")
        for ligne in lignes:
            propre = ligne.rstrip()
            if propre:
                try:
                    self._file.put_nowait(propre)
                except queue.Full:
                    pass
        return len(texte)

    def flush(self) -> None:
        try:
            self._origine.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return False


class PontJournal:
    """Installe/retire les captures et sert les lignes accumulées.

    Deux sources, parce que le code en a deux :
      - le logger **racine** — les scrapers construisent leurs loggers avec
        ``setup_logger`` (niveau DEBUG, ``propagate`` par défaut), leurs messages
        remontent donc ici sans qu'aucun d'eux soit modifié ;
      - ``sys.stdout`` — pour tout ce qui passe encore par ``print()``.
    """

    def __init__(self, taille: int = TAILLE_FILE) -> None:
        self.file: queue.Queue[str] = queue.Queue(maxsize=taille)
        self._handler: HandlerFileAttente | None = None
        self._stdout_origine = None
        self._niveau_racine: int | None = None

    def installer(self) -> None:
        """Branche le pont. Sans effet s'il l'est déjà."""
        if self._handler is not None:
            return
        self._handler = HandlerFileAttente(self.file)
        racine = logging.getLogger()
        # ⚠️ Abaisser le niveau du logger RACINE, sinon la moitié du code reste
        # invisible : les scrapers historiques passent par ``setup_logger``, qui
        # force DEBUG, mais les modules récents (core/, methode_*) utilisent un
        # simple ``logging.getLogger(__name__)`` sans niveau — ils héritent donc
        # de la racine, à WARNING par défaut, et leurs INFO sont jetés AVANT
        # d'atteindre le moindre handler.
        if racine.level > logging.INFO or racine.level == logging.NOTSET:
            self._niveau_racine = racine.level
            racine.setLevel(logging.INFO)
        racine.addHandler(self._handler)
        self._stdout_origine = sys.stdout
        sys.stdout = FluxVersFile(self.file, self._stdout_origine)

    def retirer(self) -> None:
        """Débranche le pont, rend son niveau à la racine et signale les pertes."""
        if self._stdout_origine is not None:
            sys.stdout = self._stdout_origine
            self._stdout_origine = None
        if self._handler is None:
            return
        racine = logging.getLogger()
        racine.removeHandler(self._handler)
        if self._niveau_racine is not None:
            racine.setLevel(self._niveau_racine)
            self._niveau_racine = None
        if self._handler.perdues:
            # ⚠️ Faire de la PLACE : la file est pleine précisément quand des
            # lignes ont été perdues, donc un ``put_nowait`` nu échouerait — et
            # l'avertissement le plus utile serait le seul à disparaître.
            avis = (f"… {self._handler.perdues} ligne(s) omise(s) à l'écran "
                    f"(journal complet dans log/)")
            while True:
                try:
                    self.file.put_nowait(avis)
                    break
                except queue.Full:
                    try:
                        self.file.get_nowait()
                    except queue.Empty:  # vidée entre-temps par le consommateur
                        break
        self._handler = None

    def lignes(self, maximum: int = 200) -> list[str]:
        """Retire et retourne jusqu'à ``maximum`` lignes en attente."""
        sortie: list[str] = []
        for _ in range(maximum):
            try:
                sortie.append(self.file.get_nowait())
            except queue.Empty:
                break
        return sortie

    def vider(self) -> None:
        """Jette tout ce qui reste (nouveau run : on repart d'un écran propre)."""
        while True:
            try:
                self.file.get_nowait()
            except queue.Empty:
                return
