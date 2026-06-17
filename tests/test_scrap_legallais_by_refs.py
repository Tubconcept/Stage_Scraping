"""
Tests unitaires pour scrapers/Legallais_P1/products/scrap_legallais_by_refs.py

Couvre les helpers extraits lors du refactoring de _search_and_scrape()
(réduction Cognitive Complexity de 40 → 12) :
- _find_exact_match()
- _find_product_url()
- _handle_combo_index()
- _upsert_rows()
- _scrape_ref()
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.Legallais_P1.products.scrap_legallais_by_refs import (
    _find_exact_match,
    _find_product_url,
    _handle_combo_index,
    _upsert_rows,
    _scrape_ref,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_span(text):
    s = MagicMock()
    s.text = text
    return s


def _make_link(href):
    lnk = MagicMock()
    lnk.get_attribute.return_value = href
    return lnk


def _make_row(ref="REF001", is_combo="False", parent_ref=""):
    return {"productRef": ref, "isCombination": is_combo, "parentRef": parent_ref}


# ─── _find_exact_match ───────────────────────────────────────────────────────

class TestFindExactMatch:
    BASE = "https://www.legallais.com"

    def test_returns_url_on_exact_match(self):
        spans = [_make_span("  REF001  ")]
        links = [_make_link("/product/ref001")]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._to_full_url",
            side_effect=lambda h: self.BASE + h,
        ):
            result = _find_exact_match(spans, links, "REF001")
        assert result == self.BASE + "/product/ref001"

    def test_returns_none_when_no_match(self):
        spans = [_make_span("REF999")]
        links = [_make_link("/product/ref999")]
        result = _find_exact_match(spans, links, "REF001")
        assert result is None

    def test_skips_span_with_exception(self):
        bad_span = MagicMock()
        bad_span.text = MagicMock(side_effect=Exception("DOM error"))
        good_span = _make_span("REF001")
        spans = [bad_span, good_span]
        links = [_make_link("/p/1"), _make_link("/p/2")]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._to_full_url",
            side_effect=lambda h: self.BASE + h,
        ):
            result = _find_exact_match(spans, links, "REF001")
        assert result == self.BASE + "/p/2"

    def test_index_out_of_bounds_is_guarded(self):
        """If span index exceeds result_links, should not crash."""
        spans = [_make_span("REF001"), _make_span("REF002")]
        links = [_make_link("/p/1")]  # Only 1 link, 2 spans
        result = _find_exact_match(spans, links, "REF002")
        assert result is None

    def test_empty_inputs_returns_none(self):
        assert _find_exact_match([], [], "REF001") is None

    def test_first_match_wins(self):
        spans = [_make_span("REF001"), _make_span("REF001")]
        links = [_make_link("/p/first"), _make_link("/p/second")]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._to_full_url",
            side_effect=lambda h: self.BASE + h,
        ):
            result = _find_exact_match(spans, links, "REF001")
        assert result == self.BASE + "/p/first"


# ─── _find_product_url ───────────────────────────────────────────────────────

class TestFindProductUrl:
    BASE = "https://www.legallais.com"

    def _driver_with_timeout(self):
        """Driver whose wait_for_element raises for search_result."""
        driver = MagicMock()
        driver.wait_for_element.side_effect = [None, Exception("timeout")]
        return driver

    def test_returns_none_when_no_results_appear(self):
        driver = MagicMock()
        driver.wait_for_element.side_effect = [None, Exception("timeout")]
        result = _find_product_url(driver, "REF001")
        assert result is None

    def test_returns_exact_match_url(self):
        driver = MagicMock()
        span = _make_span("REF001")
        link = _make_link("/product/ref001")
        driver.select_all.side_effect = [[link], [span]]

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._find_exact_match",
            return_value=self.BASE + "/product/ref001",
        ):
            result = _find_product_url(driver, "REF001")
        assert result == self.BASE + "/product/ref001"

    def test_falls_back_to_first_result_when_no_exact_match(self):
        driver = MagicMock()
        link = _make_link("/product/fallback")
        driver.select_all.side_effect = [[link], []]

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._find_exact_match",
            return_value=None,
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._to_full_url",
            return_value=self.BASE + "/product/fallback",
        ):
            result = _find_product_url(driver, "REF001")
        assert result == self.BASE + "/product/fallback"

    def test_returns_none_when_no_links_and_no_exact_match(self):
        driver = MagicMock()
        driver.select_all.side_effect = [[], []]  # no links, no spans
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._find_exact_match",
            return_value=None,
        ):
            result = _find_product_url(driver, "REF001")
        assert result is None


# ─── _handle_combo_index ─────────────────────────────────────────────────────

class TestHandleComboIndex:
    def test_no_op_when_not_combination(self):
        rows = [_make_row(is_combo="False")]
        _handle_combo_index(rows)
        assert "combinationIndex" not in rows[0]

    def test_assigns_index_from_resolve(self):
        rows = [_make_row(is_combo="True", parent_ref="PAR")]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs.resolve_decli_index",
            return_value=5,
        ):
            _handle_combo_index(rows)
        assert rows[0]["combinationIndex"] == 5

    def test_all_rows_get_same_index(self):
        rows = [_make_row(is_combo="True"), _make_row(is_combo="True")]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs.resolve_decli_index",
            return_value=3,
        ):
            _handle_combo_index(rows)
        assert all(r["combinationIndex"] == 3 for r in rows)

    def test_exception_silently_ignored(self):
        rows = [_make_row(is_combo="True", parent_ref="PAR")]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs.resolve_decli_index",
            side_effect=Exception("DB error"),
        ):
            _handle_combo_index(rows)
        assert "combinationIndex" not in rows[0]


# ─── _upsert_rows ─────────────────────────────────────────────────────────────

class TestUpsertRows:
    def test_no_op_when_db_conn_is_none(self):
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs.upsert_product"
        ) as mock_upsert:
            _upsert_rows(None, [_make_row()], "https://example.com/p/1")
        mock_upsert.assert_not_called()

    def test_calls_upsert_for_each_row(self):
        db_conn = MagicMock()
        rows = [_make_row("R1"), _make_row("R2")]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._map_to_csv_headers",
            return_value={"product_fournisseur_url": ""},
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs.upsert_product"
        ) as mock_upsert:
            _upsert_rows(db_conn, rows, "https://example.com/p/1")
        assert mock_upsert.call_count == 2

    def test_exception_does_not_stop_remaining_rows(self):
        db_conn = MagicMock()
        rows = [_make_row("R1"), _make_row("R2"), _make_row("R3")]
        call_count = 0

        def fake_upsert(conn, site, mapped):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("write error")

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._map_to_csv_headers",
            return_value={"product_fournisseur_url": ""},
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs.upsert_product",
            side_effect=fake_upsert,
        ):
            _upsert_rows(db_conn, rows, "https://example.com/p/1")

        assert call_count == 3

    def test_product_url_set_in_mapped(self):
        db_conn = MagicMock()
        captured = {}

        def fake_upsert(conn, site, mapped):
            captured.update(mapped)

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._map_to_csv_headers",
            return_value={"product_fournisseur_url": ""},
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs.upsert_product",
            side_effect=fake_upsert,
        ):
            _upsert_rows(db_conn, [_make_row()], "https://example.com/p/99")

        assert captured["product_fournisseur_url"] == "https://example.com/p/99"


# ─── _scrape_ref ─────────────────────────────────────────────────────────────

class TestScrapeRef:
    URL = "https://example.com/product/1"

    def _driver(self):
        return MagicMock()

    def _scraper(self, rows=None):
        s = MagicMock()
        s.scrape_product.return_value = rows or []
        return s

    def test_ok_counter_incremented_on_success(self):
        rows = [_make_row()]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._find_product_url",
            return_value=self.URL,
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._handle_combo_index"
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._upsert_rows"
        ):
            result = _scrape_ref(self._scraper(rows), self._driver(), None, "REF001", count=0)
        assert result == 1

    def test_returns_unchanged_count_when_no_url(self):
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._find_product_url",
            return_value=None,
        ):
            result = _scrape_ref(self._scraper(), self._driver(), None, "REF001", count=3)
        assert result == 3

    def test_returns_unchanged_count_when_no_rows(self):
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._find_product_url",
            return_value=self.URL,
        ):
            result = _scrape_ref(self._scraper(rows=[]), self._driver(), None, "REF001", count=2)
        assert result == 2

    def test_returns_none_on_session_loss(self):
        driver = self._driver()
        driver.get.side_effect = Exception("target closed")
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._find_product_url",
            return_value=self.URL,
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._session_perdue",
            return_value=True,
        ):
            result = _scrape_ref(self._scraper(), driver, None, "REF001", count=0)
        assert result is None

    def test_returns_count_on_non_session_exception(self):
        driver = self._driver()
        driver.get.side_effect = Exception("generic error")
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._find_product_url",
            return_value=self.URL,
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._session_perdue",
            return_value=False,
        ):
            result = _scrape_ref(self._scraper(), driver, None, "REF001", count=5)
        assert result == 5

    def test_handle_combo_and_upsert_called(self):
        rows = [_make_row()]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._find_product_url",
            return_value=self.URL,
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._handle_combo_index"
        ) as mock_combo, patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._upsert_rows"
        ) as mock_upsert:
            _scrape_ref(self._scraper(rows), self._driver(), MagicMock(), "REF001", count=0)

        mock_combo.assert_called_once_with(rows)
        mock_upsert.assert_called_once()

    def test_counters_accumulate_across_calls(self):
        """Simule plusieurs appels successifs comme dans la boucle principale."""
        rows = [_make_row()]

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._find_product_url",
            return_value=self.URL,
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._handle_combo_index"
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_by_refs._upsert_rows"
        ):
            count = 0
            count = _scrape_ref(self._scraper(rows), self._driver(), None, "R1", count)
            count = _scrape_ref(self._scraper(rows), self._driver(), None, "R2", count)
            count = _scrape_ref(self._scraper([]), self._driver(), None, "R3", count)

        assert count == 2
