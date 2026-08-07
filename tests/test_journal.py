"""
Tests du pont journal → interface (gui/journal.py).

Ce pont est ce qui rend un scrape lancé depuis la GUI **observable** : sans lui,
les scrapers historiques journalisent et impriment dans le vide, et l'écran reste
muet du début à la fin. Les régressions y sont donc silencieuses par nature —
d'où ces tests.

N'importe pas Tkinter : le module ne dépend que de la stdlib.

Lancement : pytest tests/test_journal.py -v
"""

import io
import logging
import queue
import sys
import threading
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from gui.journal import (  # noqa: E402
    FluxVersFile,
    HandlerFileAttente,
    PontJournal,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Handler de journalisation
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandler:

    @staticmethod
    def _record(message: str, niveau: int = logging.INFO,
                nom: str = "prolians.produits") -> logging.LogRecord:
        return logging.LogRecord(nom, niveau, "f.py", 1, message, None, None)

    def test_capte_un_info(self):
        file: queue.Queue = queue.Queue()
        HandlerFileAttente(file).emit(self._record("42 produits"))
        assert "42 produits" in file.get_nowait()

    def test_ignore_le_debug(self):
        """Le DEBUG reste dans log/ : à l'écran il noierait l'utile."""
        file: queue.Queue = queue.Queue()
        handler = HandlerFileAttente(file)
        record = self._record("détail", logging.DEBUG)
        if record.levelno >= handler.level:  # le handler filtre par niveau
            handler.emit(record)
        assert file.empty()

    def test_prefixe_les_avertissements(self):
        file: queue.Queue = queue.Queue()
        HandlerFileAttente(file).emit(self._record("session perdue", logging.WARNING))
        assert file.get_nowait().startswith("[WARNING]")

    def test_ignore_les_loggers_tiers(self):
        """asyncio et consorts inondent l'écran sans rien apprendre à personne."""
        file: queue.Queue = queue.Queue()
        HandlerFileAttente(file).emit(self._record("selector", nom="asyncio"))
        assert file.empty()

    def test_file_pleine_compte_sans_lever(self):
        """Un handler qui bloque bloquerait le scraper : on jette et on compte."""
        file: queue.Queue = queue.Queue(maxsize=1)
        handler = HandlerFileAttente(file)
        for i in range(5):
            handler.emit(self._record(f"ligne {i}"))
        assert file.qsize() == 1
        assert handler.perdues == 4


# ═══════════════════════════════════════════════════════════════════════════════
# Capture de print()
# ═══════════════════════════════════════════════════════════════════════════════

class TestFluxVersFile:

    def test_decoupe_en_lignes(self):
        file: queue.Queue = queue.Queue()
        flux = FluxVersFile(file, io.StringIO())
        flux.write("a\nb\n")
        assert [file.get_nowait(), file.get_nowait()] == ["a", "b"]

    def test_ligne_partielle_attend_son_saut(self):
        """``print`` écrit le texte puis le « \\n » : ne pas couper au milieu."""
        file: queue.Queue = queue.Queue()
        flux = FluxVersFile(file, io.StringIO())
        flux.write("Session Prolians")
        assert file.empty()
        flux.write(" chargée\n")
        assert file.get_nowait() == "Session Prolians chargée"

    def test_lignes_vides_ignorees(self):
        file: queue.Queue = queue.Queue()
        FluxVersFile(file, io.StringIO()).write("\n\n  \n")
        assert file.empty()

    def test_alimente_toujours_le_flux_d_origine(self):
        """En ligne de commande, la sortie console ne doit rien perdre."""
        origine = io.StringIO()
        FluxVersFile(queue.Queue(), origine).write("visible\n")
        assert origine.getvalue() == "visible\n"

    def test_survit_a_un_flux_d_origine_casse(self):
        """Lancé sans console (pythonw), stdout d'origine peut lever."""
        class Casse:
            def write(self, _):
                raise OSError("pas de console")

            def flush(self):
                raise OSError("pas de console")

        file: queue.Queue = queue.Queue()
        flux = FluxVersFile(file, Casse())
        flux.write("quand même\n")
        flux.flush()
        assert file.get_nowait() == "quand même"

    def test_file_pleine_ne_leve_pas(self):
        file: queue.Queue = queue.Queue(maxsize=1)
        flux = FluxVersFile(file, io.StringIO())
        flux.write("un\ndeux\ntrois\n")
        assert file.qsize() == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Le pont complet
# ═══════════════════════════════════════════════════════════════════════════════

class TestPontJournal:

    @pytest.fixture
    def pont(self):
        pont = PontJournal()
        yield pont
        pont.retirer()  # jamais laisser stdout détourné entre deux tests

    def test_capte_logging_et_print(self, pont):
        pont.installer()
        logging.getLogger("prolians.test").info("par le logger")
        print("par print")
        pont.retirer()
        capte = "\n".join(pont.lignes())
        assert "par le logger" in capte
        assert "par print" in capte

    def test_capte_un_logger_sans_niveau(self):
        """Cas des modules récents : ``getLogger(__name__)`` sans ``setLevel``.

        Ils héritent du logger racine (WARNING par défaut) : sans abaissement de
        ce niveau, leurs INFO sont jetés AVANT d'atteindre le moindre handler et
        toute la voie « Méthodes » resterait muette à l'écran.
        """
        racine = logging.getLogger()
        avant = racine.level
        racine.setLevel(logging.WARNING)
        pont = PontJournal()
        try:
            pont.installer()
            logging.getLogger("core.methodes.test").info("voie rapide démarrée")
            pont.retirer()
            assert any("voie rapide démarrée" in ligne for ligne in pont.lignes())
            assert racine.level == logging.WARNING  # niveau rendu à la racine
        finally:
            pont.retirer()
            racine.setLevel(avant)

    def test_capte_depuis_un_thread(self, pont):
        """Le scraper tourne dans un thread de travail : c'est le cas réel."""
        pont.installer()

        def travail():
            logging.getLogger("prolians.test").info("depuis le thread")
            print("print depuis le thread")

        fil = threading.Thread(target=travail)
        fil.start()
        fil.join()
        pont.retirer()
        capte = "\n".join(pont.lignes())
        assert "depuis le thread" in capte
        assert "print depuis le thread" in capte

    def test_retirer_restaure_stdout(self, pont):
        origine = sys.stdout
        pont.installer()
        assert sys.stdout is not origine
        pont.retirer()
        assert sys.stdout is origine

    def test_ne_capte_plus_apres_retrait(self, pont):
        pont.installer()
        pont.retirer()
        pont.vider()
        logging.getLogger("prolians.test").info("trop tard")
        print("trop tard aussi")
        assert pont.lignes() == []

    def test_installer_deux_fois_est_sans_effet(self, pont):
        """Un double branchement dupliquerait chaque ligne à l'écran."""
        pont.installer()
        detourne = sys.stdout
        pont.installer()
        assert sys.stdout is detourne
        pont.retirer()
        assert sys.stdout is not detourne

    def test_lignes_bornees(self, pont):
        for i in range(50):
            pont.file.put_nowait(f"ligne {i}")
        assert len(pont.lignes(maximum=10)) == 10
        assert len(pont.lignes(maximum=100)) == 40

    def test_vider(self, pont):
        for i in range(5):
            pont.file.put_nowait(str(i))
        pont.vider()
        assert pont.lignes() == []

    def test_signale_les_lignes_perdues(self, pont):
        """Une rafale tronquée doit se voir : sinon l'écran ment par omission."""
        petit = PontJournal(taille=2)
        petit.installer()
        for i in range(20):
            logging.getLogger("prolians.test").info("ligne %d", i)
        petit.retirer()
        assert any("omise" in ligne for ligne in petit.lignes())
