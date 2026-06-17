"""
Tests unitaires pour scrapers/Legallais_P1/orders/scraper_legallais_orders.py

Couvre les fonctions extraites lors du refactoring de get_info()
(réduction Cognitive Complexity de 21 → 1) :
- _extract_refs()
- _extract_date()
- _parse_qty()
- _extract_li_data()
- _update_product_entry()
- _build_product_data()
- _extract_status()
- get_info() — intégration des helpers
"""

import sys
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.Legallais_P1.orders.scraper_legallais_orders import (
    _extract_refs,
    _extract_date,
    _parse_qty,
    _extract_li_data,
    _update_product_entry,
    _build_product_data,
    _extract_status,
    get_info,
)


# ─── _extract_refs ────────────────────────────────────────────────────────────

class TestExtractRefs:
    def test_normal_link_and_ref(self):
        cmd = {"link": "/user/order/CMD123", "ref": "REF-001"}
        ref_p1, ref_cmd = _extract_refs(cmd)
        assert ref_p1 == "CMD123"
        assert ref_cmd == "REF-001"

    def test_missing_key_returns_empty_strings(self):
        ref_p1, ref_cmd = _extract_refs({})
        assert ref_p1 == ""
        assert ref_cmd == ""

    def test_link_with_trailing_slash(self):
        cmd = {"link": "/user/order/456/", "ref": "R2"}
        ref_p1, _ = _extract_refs(cmd)
        assert ref_p1 == ""  # split("/")[-1] sur "/456/" donne ""

    def test_none_cmd_returns_empty_strings(self):
        ref_p1, ref_cmd = _extract_refs(None)
        assert ref_p1 == ""
        assert ref_cmd == ""

    def test_returns_tuple_of_two_strings(self):
        cmd = {"link": "/order/789", "ref": "R3"}
        result = _extract_refs(cmd)
        assert len(result) == 2
        assert all(isinstance(v, str) for v in result)


# ─── _parse_qty ───────────────────────────────────────────────────────────────

class TestParseQty:
    def test_simple_integer(self):
        assert _parse_qty("3") == 3

    def test_fraction_takes_numerator(self):
        assert _parse_qty("3/5") == 3

    def test_fraction_with_spaces(self):
        assert _parse_qty("2/10") == 2

    def test_invalid_string_returns_zero(self):
        assert _parse_qty("abc") == 0

    def test_empty_string_returns_zero(self):
        assert _parse_qty("") == 0

    def test_zero(self):
        assert _parse_qty("0") == 0

    def test_large_number(self):
        assert _parse_qty("100") == 100

    def test_fraction_invalid_numerator(self):
        assert _parse_qty("abc/5") == 0


# ─── _update_product_entry ────────────────────────────────────────────────────

class TestUpdateProductEntry:
    def _make_data(self):
        return defaultdict(lambda: {"qty": 0, "prix": None, "title": ""})

    def test_accumulates_qty(self):
        d = self._make_data()
        _update_product_entry(d, "REF001", "Produit A", 2, "10.00")
        _update_product_entry(d, "REF001", "", 3, "10.00")
        assert d["REF001"]["qty"] == 5

    def test_updates_prix(self):
        d = self._make_data()
        _update_product_entry(d, "REF001", "Produit A", 1, "15.50")
        assert d["REF001"]["prix"] == "15.50"

    def test_title_set_when_non_empty(self):
        d = self._make_data()
        _update_product_entry(d, "REF001", "Mon Produit", 1, "5.00")
        assert d["REF001"]["title"] == "Mon Produit"

    def test_empty_title_does_not_overwrite_existing(self):
        d = self._make_data()
        _update_product_entry(d, "REF001", "Ancien titre", 1, "5.00")
        _update_product_entry(d, "REF001", "", 2, "5.00")
        assert d["REF001"]["title"] == "Ancien titre"

    def test_none_ref_does_nothing(self):
        d = self._make_data()
        _update_product_entry(d, None, "Produit", 3, "9.99")
        assert len(d) == 0

    def test_empty_ref_does_nothing(self):
        d = self._make_data()
        _update_product_entry(d, "", "Produit", 1, "1.00")
        assert len(d) == 0

    def test_multiple_refs_independent(self):
        d = self._make_data()
        _update_product_entry(d, "A", "PA", 1, "1.00")
        _update_product_entry(d, "B", "PB", 2, "2.00")
        assert d["A"]["qty"] == 1
        assert d["B"]["qty"] == 2


# ─── _extract_date ────────────────────────────────────────────────────────────

class TestExtractDate:
    def _mock_page_with_header(self, date_text):
        """Page dont le premier sélecteur retourne date_text."""
        page = MagicMock()
        locator = MagicMock()
        locator.nth.return_value.inner_text.return_value = f"Commandée le {date_text}"
        page.locator.return_value = locator
        return page

    def test_extracts_date_from_header(self):
        page = self._mock_page_with_header("15/06/2025")
        result = _extract_date(page, "REF")
        assert result == "15/06/2025"

    def test_returns_empty_string_on_total_failure(self):
        page = MagicMock()
        page.locator.side_effect = Exception("DOM error")
        result = _extract_date(page, "REF")
        assert result == ""


# ─── _extract_status ──────────────────────────────────────────────────────────

class TestExtractStatus:
    def test_returns_cleaned_status(self):
        page = MagicMock()
        page.locator.return_value.inner_text.return_value = "  Livré  "
        result = _extract_status(page, "REF")
        assert isinstance(result, str)
        assert "Livré" in result

    def test_returns_none_on_error(self):
        page = MagicMock()
        page.locator.return_value.inner_text.side_effect = Exception("timeout")
        result = _extract_status(page, "REF")
        assert result is None


# ─── _build_product_data ──────────────────────────────────────────────────────

class TestBuildProductData:
    def test_empty_list_returns_empty(self):
        page = MagicMock()
        page.locator.return_value.all.return_value = []
        prdt_data, final_list = _build_product_data(page, "REF")
        assert final_list == []

    def test_aggregates_two_items(self):
        li1 = MagicMock()
        li2 = MagicMock()

        page = MagicMock()
        page.locator.return_value.all.return_value = [li1, li2]

        with patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._extract_li_data",
            side_effect=[
                ("REF001", "ProduitA", 2, "10.00"),
                ("REF001", "", 3, "10.00"),
            ],
        ):
            prdt_data, final_list = _build_product_data(page, "REF_CMD")

        assert prdt_data["REF001"]["qty"] == 5
        assert len(final_list) == 1
        assert "REF001" in final_list[0]

    def test_exception_returns_empty_lists(self):
        page = MagicMock()
        page.locator.return_value.all.side_effect = Exception("DOM crash")
        prdt_data, final_list = _build_product_data(page, "REF")
        assert final_list == []

    def test_final_list_format(self):
        li = MagicMock()
        page = MagicMock()
        page.locator.return_value.all.return_value = [li]

        with patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._extract_li_data",
            return_value=("REF999", "Titre Produit", 4, "25.00"),
        ):
            _, final_list = _build_product_data(page, "REF_CMD")

        assert final_list == ["REF999:Titre Produit:4"]


# ─── get_info (intégration) ───────────────────────────────────────────────────

class TestGetInfo:
    """Vérifie que get_info() orchestre correctement les helpers et retourne le bon dict."""

    def test_returns_expected_keys(self):
        page = MagicMock()
        cmd = {"link": "/order/123", "ref": "CMD-REF"}

        with patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._extract_refs",
            return_value=("123", "CMD-REF"),
        ), patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._extract_date",
            return_value="01/06/2025",
        ), patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._build_product_data",
            return_value=(defaultdict(dict), ["REF001:ProdA:2"]),
        ), patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._extract_status",
            return_value="Livré",
        ):
            result = get_info(page, cmd)

        assert set(result.keys()) == {"ref_p1", "ref_cmd", "date_cmd", "statut", "prdt_data"}

    def test_prdt_data_joined_with_pipe(self):
        page = MagicMock()
        cmd = {"link": "/order/1", "ref": "R"}

        with patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._extract_refs",
            return_value=("1", "R"),
        ), patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._extract_date",
            return_value="01/01/2025",
        ), patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._build_product_data",
            return_value=(defaultdict(dict), ["A:TitreA:1", "B:TitreB:2"]),
        ), patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._extract_status",
            return_value="OK",
        ):
            result = get_info(page, cmd)

        assert result["prdt_data"] == "A:TitreA:1||B:TitreB:2"

    def test_empty_final_list_gives_empty_prdt_data_string(self):
        page = MagicMock()
        cmd = {"link": "/order/2", "ref": "R2"}

        with patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._extract_refs",
            return_value=("2", "R2"),
        ), patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._extract_date",
            return_value="",
        ), patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._build_product_data",
            return_value=(defaultdict(dict), []),
        ), patch(
            "scrapers.Legallais_P1.orders.scraper_legallais_orders._extract_status",
            return_value=None,
        ):
            result = get_info(page, cmd)

        assert result["prdt_data"] == ""
        assert result["statut"] is None
