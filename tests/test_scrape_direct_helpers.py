"""
Tests unitaires pour les helpers de _scrape_direct dans
scrapers/Legallais_P1/products/scrap_legallais_products.py

Couvre la réduction Cognitive Complexity de 133 → ≤15 sur _scrape_direct()
via extraction de 5 helpers :
- _browse_one_product()
- _browse_one_page()
- _browse_category()
- _run_browse_mode()
- _run_search_mode()
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scrapers.Legallais_P1.products.scrap_legallais_products as _mod
from scrapers.Legallais_P1.products.scrap_legallais_products import (
    _browse_one_product,
    _browse_one_page,
    _browse_category,
    _run_browse_mode,
    _run_search_mode,
)


# ─── Helpers de fabrication ───────────────────────────────────────────────────

def _make_row(ref="REF001", is_combo="False", parent_ref=""):
    return {"productRef": ref, "isCombination": is_combo, "parentRef": parent_ref}


def _make_scraper(rows=None, categories=None, pages=1, links=None):
    s = MagicMock()
    s.scrape_product.return_value = rows if rows is not None else []
    s.get_categories.return_value = categories or []
    s.get_page_count.return_value = pages
    s.get_product_links.return_value = links or []
    s.get_breadcrumb_categories.return_value = ("C1", "C2", "C3")
    return s


def _make_driver():
    return MagicMock()


# ─── _browse_one_product ─────────────────────────────────────────────────────

class TestBrowseOneProduct:
    URL = "https://www.legallais.com/product/ref001"

    def _call(self, driver=None, scraper=None, db_conn=None,
              href="/product/ref001", cat1="C1", cat2="C2", cat3="C3",
              ok=0, err=0, visited_urls=None):
        return _browse_one_product(
            driver or _make_driver(),
            scraper or _make_scraper(),
            db_conn,
            href, cat1, cat2, cat3,
            ok, err,
            visited_urls if visited_urls is not None else set(),
        )

    def test_skips_already_visited_url(self):
        visited = {self.URL}
        result = self._call(href=self.URL, ok=3, err=1, visited_urls=visited)
        assert result == (3, 1)

    def test_ok_incremented_on_success(self):
        rows = [_make_row()]
        with patch.object(_mod, "_assign_combination_index"), \
             patch.object(_mod, "_persist_rows"):
            result = self._call(scraper=_make_scraper(rows=rows), ok=0, err=0)
        assert result == (1, 0)

    def test_err_incremented_when_no_rows(self):
        result = self._call(scraper=_make_scraper(rows=[]), ok=0, err=0)
        assert result == (0, 1)

    def test_returns_none_on_session_loss(self):
        driver = _make_driver()
        driver.get.side_effect = Exception("target closed")
        with patch.object(_mod, "_session_perdue", return_value=True):
            result = self._call(driver=driver, ok=5, err=2)
        assert result is None

    def test_err_incremented_on_non_session_exception(self):
        driver = _make_driver()
        driver.get.side_effect = Exception("generic")
        with patch.object(_mod, "_session_perdue", return_value=False):
            result = self._call(driver=driver, ok=2, err=1)
        assert result == (2, 2)

    def test_url_added_to_visited_set(self):
        visited = set()
        rows = [_make_row()]
        with patch.object(_mod, "_assign_combination_index"), \
             patch.object(_mod, "_persist_rows"):
            self._call(href="/product/ref001", scraper=_make_scraper(rows=rows), visited_urls=visited)
        assert self.URL in visited

    def test_assign_combination_index_called(self):
        rows = [_make_row()]
        with patch.object(_mod, "_assign_combination_index") as mock_assign, \
             patch.object(_mod, "_persist_rows"):
            self._call(scraper=_make_scraper(rows=rows), ok=3)
        mock_assign.assert_called_once_with(rows, 3)

    def test_persist_rows_called(self):
        rows = [_make_row()]
        db_conn = MagicMock()
        with patch.object(_mod, "_assign_combination_index"), \
             patch.object(_mod, "_persist_rows") as mock_persist:
            self._call(scraper=_make_scraper(rows=rows), db_conn=db_conn)
        mock_persist.assert_called_once()


# ─── _browse_one_page ─────────────────────────────────────────────────────────

class TestBrowseOnePage:
    def _call(self, scraper=None, driver=None, db_conn=None, page_num=1,
              cat1="C1", cat2="C2", cat3="C3", ok=0, err=0, visited_urls=None):
        return _browse_one_page(
            scraper or _make_scraper(),
            driver or _make_driver(),
            db_conn,
            page_num, cat1, cat2, cat3,
            ok, err,
            visited_urls if visited_urls is not None else set(),
        )

    def test_page_1_does_not_call_go_to_page(self):
        scraper = _make_scraper(links=[])
        self._call(scraper=scraper, page_num=1)
        scraper.go_to_page.assert_not_called()

    def test_page_2_calls_go_to_page(self):
        scraper = _make_scraper(links=[])
        self._call(scraper=scraper, page_num=2)
        scraper.go_to_page.assert_called_once_with(2)

    def test_returns_accumulated_counts(self):
        scraper = _make_scraper(links=["/p/1", "/p/2"])
        with patch.object(_mod, "_browse_one_product", return_value=(1, 0)) as mock_p:
            mock_p.side_effect = [(1, 0), (2, 0)]
            result = self._call(scraper=scraper, ok=0, err=0)
        assert result == (2, 0)

    def test_propagates_none_on_session_loss(self):
        scraper = _make_scraper(links=["/p/1"])
        with patch.object(_mod, "_browse_one_product", return_value=None):
            result = self._call(scraper=scraper)
        assert result is None

    def test_stop_flag_breaks_loop(self):
        scraper = _make_scraper(links=["/p/1", "/p/2", "/p/3"])
        _mod._stop_flag = True
        try:
            with patch.object(_mod, "_browse_one_product") as mock_p:
                result = self._call(scraper=scraper)
            mock_p.assert_not_called()
        finally:
            _mod._stop_flag = False


# ─── _browse_category ────────────────────────────────────────────────────────

class TestBrowseCategory:
    def _call(self, driver=None, scraper=None, db_conn=None,
              idx=1, cat_url="https://legallais.com/cat", categories=None,
              ok=0, err=0, visited_urls=None):
        return _browse_category(
            driver or _make_driver(),
            scraper or _make_scraper(),
            db_conn, idx, cat_url,
            categories or ["https://legallais.com/cat"],
            ok, err,
            visited_urls if visited_urls is not None else set(),
        )

    def test_breadcrumb_exception_is_swallowed(self):
        driver = _make_driver()
        driver.wait_for_element.side_effect = [Exception("timeout"), Exception("timeout")]
        scraper = _make_scraper(pages=0, links=[])
        result = self._call(driver=driver, scraper=scraper)
        assert result == (0, 0)

    def test_returns_updated_counts_after_pages(self):
        scraper = _make_scraper(pages=2)
        with patch.object(_mod, "_browse_one_page", side_effect=[(1, 0), (2, 0)]):
            result = self._call(scraper=scraper)
        assert result == (2, 0)

    def test_propagates_none_on_session_loss_from_page(self):
        scraper = _make_scraper(pages=1)
        with patch.object(_mod, "_browse_one_page", return_value=None):
            result = self._call(scraper=scraper)
        assert result is None

    def test_session_loss_exception_returns_none(self):
        driver = _make_driver()
        driver.get.side_effect = Exception("target closed")
        with patch.object(_mod, "_session_perdue", return_value=True):
            result = self._call(driver=driver)
        assert result is None

    def test_non_session_exception_returns_counts(self):
        driver = _make_driver()
        driver.get.side_effect = Exception("generic network error")
        with patch.object(_mod, "_session_perdue", return_value=False):
            result = self._call(driver=driver, ok=3, err=1)
        assert result == (3, 1)

    def test_stop_flag_breaks_page_loop(self):
        scraper = _make_scraper(pages=3)
        _mod._stop_flag = True
        try:
            with patch.object(_mod, "_browse_one_page") as mock_page:
                result = self._call(scraper=scraper)
            mock_page.assert_not_called()
            assert result == (0, 0)
        finally:
            _mod._stop_flag = False


# ─── _run_browse_mode ────────────────────────────────────────────────────────

class TestRunBrowseMode:
    def test_db_conn_closed_on_normal_exit(self):
        scraper = _make_scraper(categories=[])
        db_conn = MagicMock()
        with patch.object(_mod, "get_scraped_product_urls", return_value=set()):
            _run_browse_mode(_make_driver(), scraper, db_conn, None)
        db_conn.close.assert_called_once()

    def test_db_conn_closed_on_session_loss(self):
        scraper = _make_scraper(categories=["https://legallais.com/cat"])
        db_conn = MagicMock()
        with patch.object(_mod, "get_scraped_product_urls", return_value=set()), \
             patch.object(_mod, "_browse_category", return_value=None):
            _run_browse_mode(_make_driver(), scraper, db_conn, None)
        db_conn.close.assert_called_once()

    def test_filters_guides_de_choix(self):
        categories_raw = ["https://legallais.com/cat", "https://legallais.com/guides-de-choix/x"]
        scraper = _make_scraper(categories=categories_raw)
        visited_call_categories = []

        def capture_category(driver, sc, db, idx, cat_url, cats, ok, err, visited):
            visited_call_categories.append(cat_url)
            return ok, err

        with patch.object(_mod, "get_scraped_product_urls", return_value=set()), \
             patch.object(_mod, "_browse_category", side_effect=capture_category):
            _run_browse_mode(_make_driver(), scraper, None, None)

        assert "https://legallais.com/guides-de-choix/x" not in visited_call_categories
        assert "https://legallais.com/cat" in visited_call_categories

    def test_stop_flag_skips_categories(self):
        scraper = _make_scraper(categories=["https://legallais.com/cat1", "https://legallais.com/cat2"])
        _mod._stop_flag = True
        try:
            with patch.object(_mod, "get_scraped_product_urls", return_value=set()), \
                 patch.object(_mod, "_browse_category") as mock_cat:
                _run_browse_mode(_make_driver(), scraper, None, None)
            mock_cat.assert_not_called()
        finally:
            _mod._stop_flag = False

    def test_none_db_conn_handled(self):
        scraper = _make_scraper(categories=[])
        with patch.object(_mod, "get_scraped_product_urls", return_value=set()):
            _run_browse_mode(_make_driver(), scraper, None, None)


# ─── _run_search_mode ────────────────────────────────────────────────────────

class TestRunSearchMode:
    def _item(self, url="https://legallais.com/p/1", cat1="C1", cat2="C2", cat3="C3"):
        return {"url": url, "cat1": cat1, "cat2": cat2, "cat3": cat3}

    def test_db_conn_closed_after_all_items(self):
        db_conn = MagicMock()
        scraper = _make_scraper(rows=[])
        _run_search_mode(_make_driver(), scraper, db_conn, [self._item()])
        db_conn.close.assert_called_once()

    def test_db_conn_closed_on_session_loss(self):
        db_conn = MagicMock()
        driver = _make_driver()
        driver.get.side_effect = Exception("target closed")
        with patch.object(_mod, "_session_perdue", return_value=True):
            _run_search_mode(driver, _make_scraper(), db_conn, [self._item()])
        db_conn.close.assert_called_once()

    def test_session_loss_stops_loop(self):
        driver = _make_driver()
        driver.get.side_effect = Exception("target closed")
        scraper = _make_scraper(rows=[_make_row()])
        items = [self._item("https://legallais.com/p/1"), self._item("https://legallais.com/p/2")]
        with patch.object(_mod, "_session_perdue", return_value=True):
            _run_search_mode(driver, scraper, None, items)
        assert driver.get.call_count == 1

    def test_non_session_error_continues_loop(self):
        driver = _make_driver()
        driver.get.side_effect = [Exception("generic"), None]
        scraper = _make_scraper(rows=[])
        items = [self._item("https://legallais.com/p/1"), self._item("https://legallais.com/p/2")]
        with patch.object(_mod, "_session_perdue", return_value=False):
            _run_search_mode(driver, scraper, None, items)
        assert driver.get.call_count == 2

    def test_assign_combination_index_called_with_ok_zero(self):
        rows = [_make_row()]
        scraper = _make_scraper(rows=rows)
        with patch.object(_mod, "_assign_combination_index") as mock_assign, \
             patch.object(_mod, "_persist_rows"):
            _run_search_mode(_make_driver(), scraper, None, [self._item()])
        mock_assign.assert_called_once_with(rows, 0)

    def test_persist_rows_called_per_item(self):
        rows = [_make_row()]
        scraper = _make_scraper(rows=rows)
        db_conn = MagicMock()
        items = [self._item("https://legallais.com/p/1"), self._item("https://legallais.com/p/2")]
        with patch.object(_mod, "_assign_combination_index"), \
             patch.object(_mod, "_persist_rows") as mock_persist:
            _run_search_mode(_make_driver(), scraper, db_conn, items)
        assert mock_persist.call_count == 2

    def test_no_rows_does_not_call_persist(self):
        scraper = _make_scraper(rows=[])
        with patch.object(_mod, "_persist_rows") as mock_persist:
            _run_search_mode(_make_driver(), scraper, None, [self._item()])
        mock_persist.assert_not_called()

    def test_none_db_conn_handled(self):
        scraper = _make_scraper(rows=[])
        _run_search_mode(_make_driver(), scraper, None, [self._item()])
