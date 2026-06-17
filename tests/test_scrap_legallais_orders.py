"""
Tests unitaires pour scrapers/Legallais_P1/orders/scrap_legallais_orders.py

Couvre les fonctions extraites lors du refactoring (réduction Cognitive Complexity) :
- _parse_date_range()
- _collect_page_in_range()
- _paginate_and_collect()
- _advance_to_page_with_date()
- _process_all_cmds()
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import des fonctions testables (pas main, qui lance un navigateur)
from scrapers.Legallais_P1.orders.scrap_legallais_orders import (
    DATE_FORMAT,
    _parse_date_range,
    _collect_page_in_range,
    _paginate_and_collect,
    _advance_to_page_with_date,
    _process_all_cmds,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_cmd(date_str, link="/order/1"):
    return {"date_str": date_str, "link": link}


# ─── Tests : _parse_date_range ────────────────────────────────────────────────

class TestParseDateRange:
    """Vérifie le parsing des dates et les valeurs par défaut."""

    def test_valid_dates_returned(self):
        inputs = iter(["20/06/2025", "01/06/2025"])
        with patch("builtins.input", side_effect=inputs):
            dateinf, datesup = _parse_date_range()
        assert datesup == datetime(2025, 6, 20)
        assert dateinf == datetime(2025, 6, 1)

    def test_invalid_sup_falls_back_to_today(self):
        inputs = iter(["not-a-date", "01/06/2025"])
        with patch("builtins.input", side_effect=inputs):
            dateinf, datesup = _parse_date_range()
        assert dateinf == datetime(2025, 6, 1)
        # datesup doit être aujourd'hui (à la seconde près — on vérifie seulement la date)
        assert datesup.date() == datetime.today().date()

    def test_invalid_inf_falls_back_to_3_days_ago(self):
        inputs = iter(["20/06/2025", "bad-date"])
        with patch("builtins.input", side_effect=inputs):
            dateinf, datesup = _parse_date_range()
        assert datesup == datetime(2025, 6, 20)
        expected_inf = datetime.today() - timedelta(days=3)
        assert dateinf.date() == expected_inf.date()

    def test_both_invalid_returns_defaults(self):
        inputs = iter(["bad", "bad"])
        with patch("builtins.input", side_effect=inputs):
            dateinf, datesup = _parse_date_range()
        assert datesup.date() == datetime.today().date()
        assert dateinf.date() == (datetime.today() - timedelta(days=3)).date()

    def test_returns_tuple_of_two_datetimes(self):
        inputs = iter(["10/01/2025", "05/01/2025"])
        with patch("builtins.input", side_effect=inputs):
            result = _parse_date_range()
        assert len(result) == 2
        assert all(isinstance(d, datetime) for d in result)


# ─── Tests : _collect_page_in_range ──────────────────────────────────────────

class TestCollectPageInRange:
    """Vérifie le filtrage des commandes par plage de dates."""

    INF = datetime(2025, 6, 1)
    SUP = datetime(2025, 6, 30)

    def _mock_page(self, cmds):
        page = MagicMock()
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.get_url_cmd",
            return_value=cmds,
        ):
            return page

    def test_cmd_in_range_is_collected(self):
        cmd = _make_cmd("15/06/2025")
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.get_url_cmd",
            return_value=[cmd],
        ):
            result = _collect_page_in_range(MagicMock(), self.INF, self.SUP)
        assert result == [cmd]

    def test_cmd_before_range_excluded(self):
        cmd = _make_cmd("01/05/2025")
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.get_url_cmd",
            return_value=[cmd],
        ):
            result = _collect_page_in_range(MagicMock(), self.INF, self.SUP)
        assert result == []

    def test_cmd_after_range_excluded(self):
        cmd = _make_cmd("01/07/2025")
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.get_url_cmd",
            return_value=[cmd],
        ):
            result = _collect_page_in_range(MagicMock(), self.INF, self.SUP)
        assert result == []

    def test_cmd_on_boundary_inf_included(self):
        cmd = _make_cmd("01/06/2025")
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.get_url_cmd",
            return_value=[cmd],
        ):
            result = _collect_page_in_range(MagicMock(), self.INF, self.SUP)
        assert result == [cmd]

    def test_cmd_on_boundary_sup_included(self):
        cmd = _make_cmd("30/06/2025")
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.get_url_cmd",
            return_value=[cmd],
        ):
            result = _collect_page_in_range(MagicMock(), self.INF, self.SUP)
        assert result == [cmd]

    def test_cmd_with_invalid_date_always_collected(self):
        """Une commande dont la date est illisible est incluse par défaut (comportement conservateur)."""
        cmd = _make_cmd("invalid-date")
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.get_url_cmd",
            return_value=[cmd],
        ):
            result = _collect_page_in_range(MagicMock(), self.INF, self.SUP)
        assert result == [cmd]

    def test_mixed_cmds_filtered_correctly(self):
        cmds = [
            _make_cmd("15/06/2025", "/order/1"),  # in
            _make_cmd("01/05/2025", "/order/2"),  # before
            _make_cmd("20/06/2025", "/order/3"),  # in
            _make_cmd("bad-date",   "/order/4"),  # invalid → included
            _make_cmd("01/07/2025", "/order/5"),  # after
        ]
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.get_url_cmd",
            return_value=cmds,
        ):
            result = _collect_page_in_range(MagicMock(), self.INF, self.SUP)
        links = [c["link"] for c in result]
        assert "/order/1" in links
        assert "/order/3" in links
        assert "/order/4" in links
        assert "/order/2" not in links
        assert "/order/5" not in links

    def test_empty_page_returns_empty_list(self):
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.get_url_cmd",
            return_value=[],
        ):
            result = _collect_page_in_range(MagicMock(), self.INF, self.SUP)
        assert result == []


# ─── Tests : _advance_to_page_with_date ──────────────────────────────────────

class TestAdvanceToPageWithDate:
    """Vérifie la pagination jusqu'à une date cible."""

    TARGET = datetime(2025, 6, 1)

    def test_no_advance_when_date_already_visible(self):
        """Si check_date retourne True dès le départ, _next_page n'est pas appelé."""
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.check_date",
            return_value=True,
        ):
            page = MagicMock()
            # On ne peut pas appeler _next_page sans un vrai navigateur,
            # on vérifie juste que locator n'est pas touché
            _advance_to_page_with_date(page, self.TARGET)
            page.locator.assert_not_called()

    def test_stops_on_exception(self):
        """Une exception dans _next_page interrompt la boucle sans lever."""
        call_count = 0

        def check_date_side_effect(page, date):
            nonlocal call_count
            call_count += 1
            return False  # toujours False → boucle infinie sans break

        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.check_date",
            side_effect=check_date_side_effect,
        ), patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders._next_page",
            side_effect=Exception("navigation error"),
        ), patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.log_exception"
        ):
            _advance_to_page_with_date(MagicMock(), self.TARGET)

        # check_date appelé une fois avant la tentative _next_page
        assert call_count == 1


# ─── Tests : _paginate_and_collect ───────────────────────────────────────────

class TestPaginateAndCollect:
    """Vérifie que la phase 2 accumule correctement les commandes."""

    INF = datetime(2025, 6, 1)
    SUP = datetime(2025, 6, 30)

    def test_no_iteration_when_date_already_reached(self):
        """Si check_date retourne True dès le départ, aucune page n'est chargée."""
        url_cmd = []
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.check_date",
            return_value=True,
        ), patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders._next_page"
        ) as mock_next:
            _paginate_and_collect(MagicMock(), self.INF, self.SUP, url_cmd)
        mock_next.assert_not_called()
        assert url_cmd == []

    def test_collects_across_two_pages(self):
        """Les commandes des pages 2 et 3 sont ajoutées à url_cmd."""
        cmd_p2 = _make_cmd("10/06/2025", "/order/10")
        cmd_p3 = _make_cmd("05/06/2025", "/order/11")
        # check_date : False, False, True (s'arrête après page 3)
        check_results = iter([False, False, True])
        collect_results = iter([[cmd_p2], [cmd_p3]])

        url_cmd = []
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.check_date",
            side_effect=lambda *_: next(check_results),
        ), patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders._next_page",
        ), patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders._collect_page_in_range",
            side_effect=lambda *_: next(collect_results),
        ):
            _paginate_and_collect(MagicMock(), self.INF, self.SUP, url_cmd)

        assert cmd_p2 in url_cmd
        assert cmd_p3 in url_cmd

    def test_stops_on_exception(self):
        """Une exception interrompt la boucle et log l'erreur."""
        url_cmd = []
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.check_date",
            return_value=False,
        ), patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders._next_page",
            side_effect=Exception("nav error"),
        ), patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.log_exception"
        ) as mock_log_exc:
            _paginate_and_collect(MagicMock(), self.INF, self.SUP, url_cmd)

        mock_log_exc.assert_called_once()


# ─── Tests : _process_all_cmds ───────────────────────────────────────────────

class TestProcessAllCmds:
    """Vérifie le traitement et la persistance de chaque commande."""

    def test_persist_called_for_each_cmd(self):
        cmds = [_make_cmd("10/06/2025", "/order/1"), _make_cmd("11/06/2025", "/order/2")]
        page = MagicMock()
        fake_commande = {"ref_p1": "CMD001"}

        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.get_info",
            return_value=fake_commande,
        ), patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.persist_order"
        ) as mock_persist:
            _process_all_cmds(page, cmds)

        assert mock_persist.call_count == 2

    def test_exception_does_not_stop_remaining_cmds(self):
        """Une erreur sur une commande ne doit pas bloquer les suivantes."""
        cmds = [_make_cmd("10/06/2025", "/order/1"), _make_cmd("11/06/2025", "/order/2")]
        page = MagicMock()
        page.goto.side_effect = [Exception("timeout"), None]

        persist_calls = []
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.get_info",
            return_value={"ref_p1": "CMD002"},
        ), patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.persist_order",
            side_effect=lambda c: persist_calls.append(c),
        ), patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.log_exception"
        ):
            _process_all_cmds(page, cmds)

        # La deuxième commande doit avoir été persistée malgré l'erreur sur la première
        assert len(persist_calls) == 1

    def test_empty_list_does_nothing(self):
        page = MagicMock()
        with patch(
            "scrapers.Legallais_P1.orders.scrap_legallais_orders.persist_order"
        ) as mock_persist:
            _process_all_cmds(page, [])
        mock_persist.assert_not_called()
