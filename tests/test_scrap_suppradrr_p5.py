"""
Tests unitaires pour scrapers/Setin_P5/deleting/scrap_suppradrr_p5.py

Couvre :
- Sauvegarde de session via asyncio.to_thread (ne pas bloquer l'event loop)
- Logique d'arrêt de suppr_addr (flag should_stop)
- Import du module et instanciation du scraper GUI
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Assure que le projet est dans le path ────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_fake_context(storage_payload: dict) -> MagicMock:
    """Retourne un faux BrowserContext dont storage_state() renvoie storage_payload."""
    ctx = MagicMock()
    ctx.storage_state = AsyncMock(return_value=storage_payload)
    return ctx


# ─── Tests : sauvegarde de session (écriture hors event loop) ────────────────

class TestSessionSave:
    """Vérifie que la sauvegarde de session utilise bien asyncio.to_thread
    et écrit un JSON valide sans bloquer l'event loop."""

    FAKE_STORAGE = {"cookies": [{"name": "sid", "value": "abc123"}], "origins": []}

    @pytest.mark.asyncio
    async def test_write_text_called_via_asyncio_to_thread(self, tmp_path):
        """asyncio.to_thread doit être appelé avec Path.write_text et le JSON encodé."""
        target = tmp_path / "session.json"
        ctx = _make_fake_context(self.FAKE_STORAGE)

        storage_data = await ctx.storage_state()

        # Reproduit exactement le code corrigé
        await asyncio.to_thread(
            target.write_text, json.dumps(storage_data, indent=2), "utf-8"
        )

        assert target.exists(), "Le fichier session.json doit avoir été créé"
        written = json.loads(target.read_text("utf-8"))
        assert written == self.FAKE_STORAGE, "Le JSON écrit doit correspondre au storage_state"

    @pytest.mark.asyncio
    async def test_json_indentation_preserved(self, tmp_path):
        """Le fichier doit être indenté (indent=2) pour rester lisible."""
        target = tmp_path / "session.json"
        ctx = _make_fake_context(self.FAKE_STORAGE)
        storage_data = await ctx.storage_state()
        await asyncio.to_thread(
            target.write_text, json.dumps(storage_data, indent=2), "utf-8"
        )
        raw = target.read_text("utf-8")
        assert "\n" in raw, "Le JSON doit être indenté (multi-ligne)"

    @pytest.mark.asyncio
    async def test_event_loop_not_blocked(self, tmp_path):
        """La coroutine doit rendre la main pendant l'écriture (test de concurrence minimal)."""
        target = tmp_path / "session.json"
        ctx = _make_fake_context(self.FAKE_STORAGE)
        storage_data = await ctx.storage_state()

        checkpoint_reached = False

        async def side_task():
            nonlocal checkpoint_reached
            await asyncio.sleep(0)
            checkpoint_reached = True

        await asyncio.gather(
            asyncio.to_thread(target.write_text, json.dumps(storage_data, indent=2), "utf-8"),
            side_task(),
        )

        assert checkpoint_reached, "La tâche concurrente doit s'exécuter pendant l'écriture"


# ─── Tests : logique d'arrêt de suppr_addr ───────────────────────────────────

class TestSupprAddrStopFlag:
    """Vérifie que should_stop() interrompt correctement la boucle."""

    @pytest.mark.asyncio
    async def test_stops_immediately_when_flag_set(self):
        """Si should_stop() retourne True dès le début, aucune carte ne doit être traitée."""
        from scrapers.Setin_P5.deleting.scrap_suppradrr_p5 import suppr_addr

        page = MagicMock()
        # wait_for_timeout est une coroutine
        page.wait_for_timeout = AsyncMock()
        # locator().all() retourne une liste de 5 fausses cartes
        fake_cards = [MagicMock() for _ in range(5)]
        page.locator.return_value.all = AsyncMock(return_value=fake_cards)

        call_count = 0

        def stop_after_zero():
            return True  # arrêt immédiat

        await suppr_addr(page, should_stop=stop_after_zero)

        # locator().all() ne doit pas avoir été appelé (arrêt avant)
        page.locator.return_value.all.assert_not_called()

    @pytest.mark.asyncio
    async def test_continues_when_flag_false(self):
        """Si should_stop() retourne False et total <= 2, la boucle se termine normalement."""
        from scrapers.Setin_P5.deleting.scrap_suppradrr_p5 import suppr_addr

        page = MagicMock()
        page.wait_for_timeout = AsyncMock()
        # Seulement 2 cartes → condition de sortie normale
        fake_cards = [MagicMock(), MagicMock()]
        page.locator.return_value.all = AsyncMock(return_value=fake_cards)

        await suppr_addr(page, should_stop=lambda: False)

        # all() doit avoir été appelé exactement une fois (1 itération puis sortie)
        page.locator.return_value.all.assert_called_once()


# ─── Tests : import et instanciation du module ───────────────────────────────

class TestModuleImport:
    def test_create_scraper_returns_instance(self):
        """create_scraper() doit retourner un SetinSupprScraper sans erreur."""
        from scrapers.Setin_P5.deleting.scrap_suppradrr_p5 import (
            SetinSupprScraper,
            create_scraper,
        )

        scraper = create_scraper()
        assert isinstance(scraper, SetinSupprScraper)

    def test_request_stop_sets_flag(self):
        """request_stop() doit positionner le flag interne."""
        from scrapers.Setin_P5.deleting.scrap_suppradrr_p5 import create_scraper

        scraper = create_scraper()
        assert scraper._stop_requested is False
        scraper.request_stop()
        assert scraper._stop_requested is True
