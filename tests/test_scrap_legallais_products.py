"""
Tests unitaires pour scrapers/Legallais_P1/products/scrap_legallais_products.py

Couvre les helpers extraits lors du refactoring de _scrape_batch()
(réduction Cognitive Complexity de 20 → 3) :
- _assign_combination_index()
- _persist_rows()
- _process_item()
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.Legallais_P1.products.scrap_legallais_products import (
    _assign_combination_index,
    _persist_rows,
    _process_item,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_row(ref="REF001", is_combo="False", parent_ref=""):
    return {
        "productRef": ref,
        "isCombination": is_combo,
        "parentRef": parent_ref,
        "EAN": "",
        "productTitle": "Produit Test",
        "productPrice": "10.00",
    }


def _make_item(url="https://example.com/product/1", cat1="C1", cat2="C2", cat3="C3"):
    return {"url": url, "cat1": cat1, "cat2": cat2, "cat3": cat3}


# ─── _assign_combination_index ───────────────────────────────────────────────

class TestAssignCombinationIndex:
    def test_no_op_when_not_combination(self):
        rows = [_make_row(is_combo="False")]
        _assign_combination_index(rows, ok=0)
        assert "combinationIndex" not in rows[0]

    def test_assigns_index_from_resolve_decli(self):
        rows = [_make_row(is_combo="True", parent_ref="PAR001")]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products.resolve_decli_index",
            return_value=42,
        ):
            _assign_combination_index(rows, ok=0)
        assert rows[0]["combinationIndex"] == 42

    def test_fallback_to_ok_plus_1_on_exception(self):
        rows = [_make_row(is_combo="True", parent_ref="PAR002")]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products.resolve_decli_index",
            side_effect=Exception("DB error"),
        ):
            _assign_combination_index(rows, ok=4)
        assert rows[0]["combinationIndex"] == 5  # ok + 1

    def test_all_rows_get_same_index(self):
        rows = [
            _make_row(ref="R1", is_combo="True", parent_ref="PAR"),
            _make_row(ref="R2", is_combo="True", parent_ref="PAR"),
            _make_row(ref="R3", is_combo="True", parent_ref="PAR"),
        ]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products.resolve_decli_index",
            return_value=7,
        ):
            _assign_combination_index(rows, ok=0)
        assert all(r["combinationIndex"] == 7 for r in rows)

    def test_empty_parent_ref_still_calls_resolve(self):
        rows = [_make_row(is_combo="True", parent_ref="")]
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products.resolve_decli_index",
            return_value=1,
        ) as mock_resolve:
            _assign_combination_index(rows, ok=0)
        mock_resolve.assert_called_once_with("legallais", "")


# ─── _persist_rows ───────────────────────────────────────────────────────────

class TestPersistRows:
    def test_no_op_when_db_conn_is_none(self):
        rows = [_make_row()]
        item = _make_item()
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products.insert_product"
        ) as mock_insert:
            _persist_rows(None, rows, item, "C1", "C2", "C3", batch_id=1)
        mock_insert.assert_not_called()

    def test_inserts_each_row(self):
        rows = [_make_row("R1"), _make_row("R2")]
        item = _make_item()
        db_conn = MagicMock()
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._map_to_csv_headers",
            return_value={"product_fournisseur_url": ""},
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products.insert_product"
        ) as mock_insert:
            _persist_rows(db_conn, rows, item, "C1", "C2", "C3", batch_id=1)
        assert mock_insert.call_count == 2

    def test_db_exception_does_not_stop_remaining_rows(self):
        rows = [_make_row("R1"), _make_row("R2"), _make_row("R3")]
        item = _make_item()
        db_conn = MagicMock()
        insert_calls = []

        def fake_insert(conn, site, mapped):
            insert_calls.append(mapped)
            if len(insert_calls) == 1:
                raise Exception("DB write error")

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._map_to_csv_headers",
            return_value={"product_fournisseur_url": ""},
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products.insert_product",
            side_effect=fake_insert,
        ):
            _persist_rows(db_conn, rows, item, "C1", "C2", "C3", batch_id=1)

        # Toutes les rows tentées malgré l'erreur sur la 1ère
        assert len(insert_calls) == 3

    def test_variant_url_set_for_combination(self):
        row = _make_row("REF999", is_combo="True")
        item = _make_item(url="https://site.com/wi693165")
        db_conn = MagicMock()
        captured = {}

        def fake_insert(conn, site, mapped):
            captured.update(mapped)

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._map_to_csv_headers",
            return_value={"product_fournisseur_url": ""},
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products.insert_product",
            side_effect=fake_insert,
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._variant_url",
            return_value="https://site.com/wi999999",
        ):
            _persist_rows(db_conn, [row], item, "C1", "C2", "C3", batch_id=1)

        assert captured.get("product_fournisseur_url") == "https://site.com/wi999999"

    def test_empty_rows_does_nothing(self):
        db_conn = MagicMock()
        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products.insert_product"
        ) as mock_insert:
            _persist_rows(db_conn, [], _make_item(), "C1", "C2", "C3", batch_id=1)
        mock_insert.assert_not_called()


# ─── _process_item ───────────────────────────────────────────────────────────

class TestProcessItem:
    def _make_driver(self, rows=None, get_raises=None):
        driver = MagicMock()
        if get_raises:
            driver.get.side_effect = get_raises
        return driver

    def _make_scraper(self, rows=None):
        scraper = MagicMock()
        scraper.scrape_product.return_value = rows or []
        return scraper

    def test_ok_incremented_on_success(self):
        rows = [_make_row()]
        driver = self._make_driver()
        scraper = self._make_scraper(rows)
        item = _make_item()

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._assign_combination_index"
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._persist_rows"
        ):
            ok, err = _process_item(scraper, driver, None, item, ok=0, err=0, batch_id=1)

        assert ok == 1
        assert err == 0

    def test_err_incremented_when_no_rows(self):
        driver = self._make_driver()
        scraper = self._make_scraper(rows=[])
        item = _make_item()

        ok, err = _process_item(scraper, driver, None, item, ok=0, err=0, batch_id=1)

        assert ok == 0
        assert err == 1

    def test_err_incremented_on_driver_exception(self):
        driver = self._make_driver(get_raises=Exception("navigation crash"))
        scraper = self._make_scraper()
        item = _make_item()

        ok, err = _process_item(scraper, driver, None, item, ok=0, err=0, batch_id=1)

        assert ok == 0
        assert err == 1

    def test_wait_for_element_exception_does_not_block(self):
        """Une exception sur wait_for_element ne doit pas interrompre le traitement."""
        rows = [_make_row()]
        driver = MagicMock()
        driver.wait_for_element.side_effect = Exception("timeout")
        scraper = self._make_scraper(rows)
        item = _make_item()

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._assign_combination_index"
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._persist_rows"
        ):
            ok, err = _process_item(scraper, driver, None, item, ok=0, err=0, batch_id=1)

        assert ok == 1
        assert err == 0

    def test_assign_combination_index_called(self):
        rows = [_make_row()]
        driver = self._make_driver()
        scraper = self._make_scraper(rows)
        item = _make_item()

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._assign_combination_index"
        ) as mock_assign, patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._persist_rows"
        ):
            _process_item(scraper, driver, None, item, ok=2, err=0, batch_id=1)

        mock_assign.assert_called_once_with(rows, 2)

    def test_persist_rows_called_with_correct_categories(self):
        rows = [_make_row()]
        driver = self._make_driver()
        scraper = self._make_scraper(rows)
        item = _make_item(cat1="Electricité", cat2="Câbles", cat3="Mono")
        db_conn = MagicMock()

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._assign_combination_index"
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._persist_rows"
        ) as mock_persist:
            _process_item(scraper, driver, db_conn, item, ok=0, err=0, batch_id=1)

        mock_persist.assert_called_once_with(
            db_conn, rows, item, "Electricité", "Câbles", "Mono", 1
        )

    def test_counters_accumulate_across_multiple_calls(self):
        """Simule plusieurs appels successifs comme dans la boucle de _scrape_batch."""
        rows = [_make_row()]
        driver = MagicMock()
        scraper = MagicMock()

        with patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._assign_combination_index"
        ), patch(
            "scrapers.Legallais_P1.products.scrap_legallais_products._persist_rows"
        ):
            scraper.scrape_product.return_value = rows
            ok, err = _process_item(scraper, driver, None, _make_item(), 0, 0, 1)
            ok, err = _process_item(scraper, driver, None, _make_item(), ok, err, 1)

            scraper.scrape_product.return_value = []
            ok, err = _process_item(scraper, driver, None, _make_item(), ok, err, 1)

        assert ok == 2
        assert err == 1
