"""
Tests unitaires pour les helpers de navigation catégories dans
scrapers/Legallais_P1/products/scraper_legallais_products.py

Couvre la réduction Cognitive Complexity de 23 → ≤6 sur get_categories()
via extraction de :
- _collect_l3_urls()
- _collect_l2_urls()
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scrapers.Legallais_P1.products.scraper_legallais_products as _mod
from scrapers.Legallais_P1.products.scraper_legallais_products import (
    _collect_l3_urls,
    _collect_l2_urls,
    LegallaisScraper,
)

# Sélecteurs factices : les helpers acceptent n'importe quel dict
_SEL = {
    "menu_level1": ".lvl1",
    "menu_level2": ".lvl2",
    "menu_level3": ".lvl3",
}


# ─── Helpers de fabrication ───────────────────────────────────────────────────

def _make_item(text="Category", href=None):
    item = MagicMock()
    item.text = text
    item.get_attribute.return_value = href
    return item


def _make_driver_l3(l2_items=None, l3_items=None):
    d = MagicMock()
    def _sel(sel, timeout):
        if sel == ".lvl2":
            return l2_items or []
        if sel == ".lvl3":
            return l3_items or []
        return []
    d.select_all.side_effect = _sel
    return d


# ─── _collect_l3_urls ────────────────────────────────────────────────────────

class TestCollectL3Urls:
    def test_appends_l3_hrefs(self):
        l3a = _make_item(href="https://site.com/cat/a")
        l3b = _make_item(href="https://site.com/cat/b")
        l2  = _make_item()
        d   = _make_driver_l3(l3_items=[l3a, l3b])
        urls = []
        _collect_l3_urls(l2, d, _SEL, urls)
        assert urls == ["https://site.com/cat/a", "https://site.com/cat/b"]

    def test_skips_l3_item_with_none_href(self):
        l3_with = _make_item(href="https://site.com/cat/a")
        l3_none = _make_item(href=None)
        l2 = _make_item()
        d  = _make_driver_l3(l3_items=[l3_with, l3_none])
        urls = []
        _collect_l3_urls(l2, d, _SEL, urls)
        assert urls == ["https://site.com/cat/a"]

    def test_fallback_to_l2_href_on_exception(self):
        l2 = _make_item(href="https://site.com/fallback")
        l2.click.side_effect = Exception("click failed")
        d = _make_driver_l3()
        urls = []
        _collect_l3_urls(l2, d, _SEL, urls)
        assert urls == ["https://site.com/fallback"]

    def test_fallback_none_href_not_appended(self):
        l2 = _make_item(href=None)
        l2.click.side_effect = Exception("click failed")
        d = _make_driver_l3()
        urls = []
        _collect_l3_urls(l2, d, _SEL, urls)
        assert urls == []

    def test_no_l3_items_does_nothing(self):
        l2 = _make_item(href="https://site.com/l2")
        d  = _make_driver_l3(l3_items=[])
        urls = []
        _collect_l3_urls(l2, d, _SEL, urls)
        assert urls == []

    def test_accumulates_into_existing_list(self):
        l3 = _make_item(href="https://site.com/new")
        l2 = _make_item()
        d  = _make_driver_l3(l3_items=[l3])
        urls = ["https://site.com/existing"]
        _collect_l3_urls(l2, d, _SEL, urls)
        assert urls == ["https://site.com/existing", "https://site.com/new"]


# ─── _collect_l2_urls ────────────────────────────────────────────────────────

class TestCollectL2Urls:
    def test_calls_collect_l3_for_each_l2(self):
        l2a = _make_item(href="https://site.com/l2/a")
        l2b = _make_item(href="https://site.com/l2/b")
        l1  = _make_item()
        l3  = _make_item(href="https://site.com/l3/x")
        d   = _make_driver_l3(l2_items=[l2a, l2b], l3_items=[l3])
        urls = []
        _collect_l2_urls(l1, d, _SEL, urls)
        assert "https://site.com/l3/x" in urls

    def test_exception_on_l1_click_swallowed(self):
        l1 = _make_item()
        l1.click.side_effect = Exception("nav error")
        d  = _make_driver_l3()
        urls = []
        _collect_l2_urls(l1, d, _SEL, urls)
        assert urls == []

    def test_no_l2_items_does_nothing(self):
        l1 = _make_item()
        d  = _make_driver_l3(l2_items=[])
        urls = []
        _collect_l2_urls(l1, d, _SEL, urls)
        assert urls == []

    def test_accumulates_across_l2_items(self):
        l2a = _make_item()
        l2b = _make_item()
        l1  = _make_item()
        call_num = 0

        def sel_side_effect(sel, timeout):
            nonlocal call_num
            if sel == ".lvl2":
                return [l2a, l2b]
            call_num += 1
            return [_make_item(href=f"https://site.com/l3/{call_num}")]

        d = MagicMock()
        d.select_all.side_effect = sel_side_effect
        urls = []
        _collect_l2_urls(l1, d, _SEL, urls)
        assert len(urls) == 2


# ─── LegallaisScraper.get_categories ────────────────────────────────────────

class TestGetCategories:
    """
    get_categories() appelle _collect_l2_urls() pour chaque l1 non filtré.
    On patche _collect_l2_urls dans le module pour isoler le comportement.
    """

    def _scraper_with_driver(self, l1_items=None, raises=False):
        scraper = LegallaisScraper.__new__(LegallaisScraper)
        d = MagicMock()
        if raises:
            d.select_all.side_effect = Exception("DOM error")
        else:
            d.select_all.return_value = l1_items or []
        scraper._driver = d
        return scraper

    def test_outer_exception_swallowed_returns_empty(self):
        scraper = self._scraper_with_driver(raises=True)
        result = scraper.get_categories()
        assert result == []

    def test_no_l1_returns_empty(self):
        scraper = self._scraper_with_driver(l1_items=[])
        with patch.object(_mod, "_collect_l2_urls") as mock_c:
            result = scraper.get_categories()
        assert result == []
        mock_c.assert_not_called()

    def test_no_filter_processes_all_l1(self):
        l1a = _make_item(text="CatA")
        l1b = _make_item(text="CatB")
        scraper = self._scraper_with_driver(l1_items=[l1a, l1b])
        called = []

        def fake_collect(l1, d, sel, urls):
            called.append(l1.text)

        with patch.object(_mod, "_collect_l2_urls", side_effect=fake_collect):
            scraper.get_categories()

        assert called == ["CatA", "CatB"]

    def test_filter_skips_non_matching_l1(self):
        l1_match    = _make_item(text="Électricité")
        l1_no_match = _make_item(text="Bâtiment")
        scraper = self._scraper_with_driver(l1_items=[l1_match, l1_no_match])
        called = []

        def fake_collect(l1, d, sel, urls):
            called.append(l1.text)

        with patch.object(_mod, "_collect_l2_urls", side_effect=fake_collect):
            scraper.get_categories(category_filter="électricité")

        assert "Électricité" in called
        assert "Bâtiment" not in called

    def test_filter_case_insensitive(self):
        l1 = _make_item(text="ÉLECTRICITÉ")
        scraper = self._scraper_with_driver(l1_items=[l1])
        called = []

        with patch.object(_mod, "_collect_l2_urls", side_effect=lambda l, d, s, u: called.append(l.text)):
            scraper.get_categories(category_filter="électricité")

        assert "ÉLECTRICITÉ" in called

    def test_returns_urls_appended_by_collect(self):
        l1 = _make_item(text="Cat")
        scraper = self._scraper_with_driver(l1_items=[l1])

        def fake_collect(l1_item, driver, sel, urls):
            urls.append("https://site.com/collected")

        with patch.object(_mod, "_collect_l2_urls", side_effect=fake_collect):
            result = scraper.get_categories()

        assert result == ["https://site.com/collected"]

    def test_multiple_l1_accumulates_urls(self):
        l1a = _make_item(text="CatA")
        l1b = _make_item(text="CatB")
        scraper = self._scraper_with_driver(l1_items=[l1a, l1b])

        def fake_collect(l1, d, sel, urls):
            urls.append(f"https://site.com/{l1.text}")

        with patch.object(_mod, "_collect_l2_urls", side_effect=fake_collect):
            result = scraper.get_categories()

        assert result == ["https://site.com/CatA", "https://site.com/CatB"]
