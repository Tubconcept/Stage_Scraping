"""
Tests unitaires pour les helpers d'extraction de scrape_product() dans
scrapers/Legallais_P1/products/scraper_legallais_products.py

Couvre la réduction Cognitive Complexity de 76 → 0 sur scrape_product()
via extraction de 15 helpers + 5 constantes JS.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.Legallais_P1.products.scraper_legallais_products import (
    _extract_text,
    _extract_product_ref,
    _extract_price,
    _extract_brand_img,
    _extract_brand_name,
    _extract_js_list,
    _extract_js_scalar,
    _extract_attrs,
    _extract_ref_fab,
    _extract_ean,
    _extract_cross_sell,
    _get_combo_headers,
    _set_child_refs,
    _build_combo_row,
    _build_rows,
)

_SEL = {
    "product_reference":    ".ref",
    "product_title":        ".title",
    "price_final":          ".price",
    "price_original":       ".orig",
    "price_eco":            ".eco",
    "stock":                ".stock",
    "brand_image":          ".brand",
    "description":          ".desc",
    "characteristics_table": ".chars",
    "characteristics_ref":  ".ref-row",
    "supplier_ref":         ".sup-ref",
    "product_ean":          ".ean",
    "combinations_table":   ".combo",
    "combinations_headers": ".combo-hdr",
}


def _el(text="", attr=None):
    e = MagicMock()
    e.text = text
    e.get_attribute.return_value = attr
    return e


def _driver():
    return MagicMock()


def _clean(text):
    return text.strip()


# ─── _extract_text ────────────────────────────────────────────────────────────

class TestExtractText:
    def test_returns_clean_fn_result(self):
        d = _driver()
        d.select.return_value = _el(text="  hello  ")
        result = _extract_text(d, ".sel", str.strip)
        assert result == "hello"

    def test_returns_raw_text_without_clean_fn(self):
        d = _driver()
        d.select.return_value = _el(text="  raw  ")
        result = _extract_text(d, ".sel")
        assert result == "  raw  "

    def test_returns_empty_on_exception(self):
        d = _driver()
        d.select.side_effect = Exception("DOM error")
        assert _extract_text(d, ".sel", str.strip) == ""

    def test_returns_empty_when_selector_returns_none(self):
        d = _driver()
        d.select.return_value = None
        assert _extract_text(d, ".sel") == ""


# ─── _extract_product_ref ─────────────────────────────────────────────────────

class TestExtractProductRef:
    def test_strips_non_digits(self):
        d = _driver()
        d.select.return_value = _el(text="Réf. 693165 ABC")
        assert _extract_product_ref(d, _SEL) == "693165"

    def test_returns_empty_when_no_element(self):
        d = _driver()
        d.select.return_value = None
        assert _extract_product_ref(d, _SEL) == ""

    def test_returns_empty_on_exception(self):
        d = _driver()
        d.select.side_effect = Exception("error")
        assert _extract_product_ref(d, _SEL) == ""


# ─── _extract_price ───────────────────────────────────────────────────────────

class TestExtractPrice:
    def test_extracts_decimal_price(self):
        d = _driver()
        d.select.return_value = _el(text="15,48 € HT")
        result = _extract_price(d, _SEL)
        assert result == "15.48"

    def test_falls_back_to_second_selector(self):
        call_count = 0
        def select_side(sel, t):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("not found")
            el = _el(text="9.99€")
            return el
        d = _driver()
        d.select.side_effect = select_side
        result = _extract_price(d, _SEL)
        assert result == "9.99"

    def test_returns_empty_when_all_selectors_fail(self):
        d = _driver()
        d.select.side_effect = Exception("not found")
        assert _extract_price(d, _SEL) == ""

    def test_returns_empty_when_no_number_in_text(self):
        d = _driver()
        d.select.return_value = _el(text="Prix non disponible")
        assert _extract_price(d, _SEL) == ""

    def test_handles_nbsp_and_euro(self):
        d = _driver()
        d.select.return_value = _el(text="12\xa048 €")
        result = _extract_price(d, _SEL)
        assert result != ""


# ─── _extract_brand_img ───────────────────────────────────────────────────────

class TestExtractBrandImg:
    def test_returns_src_attribute(self):
        d = _driver()
        d.select.return_value = _el(attr="https://cdn.legallais.com/logo.png")
        assert _extract_brand_img(d, _SEL) == "https://cdn.legallais.com/logo.png"

    def test_returns_empty_when_src_is_none(self):
        d = _driver()
        d.select.return_value = _el(attr=None)
        assert _extract_brand_img(d, _SEL) == ""

    def test_returns_empty_on_exception(self):
        d = _driver()
        d.select.side_effect = Exception("error")
        assert _extract_brand_img(d, _SEL) == ""


# ─── _extract_brand_name ──────────────────────────────────────────────────────

class TestExtractBrandName:
    def test_returns_alt_attribute(self):
        d = _driver()
        el = MagicMock()
        el.get_attribute.side_effect = lambda attr: "Schneider" if attr == "alt" else None
        d.select.return_value = el
        assert _extract_brand_name(d, _SEL) == "Schneider"

    def test_falls_back_to_title_when_no_alt(self):
        d = _driver()
        el = MagicMock()
        el.get_attribute.side_effect = lambda attr: None if attr == "alt" else "Legrand"
        d.select.return_value = el
        assert _extract_brand_name(d, _SEL) == "Legrand"

    def test_returns_empty_when_no_element(self):
        d = _driver()
        d.select.return_value = None
        assert _extract_brand_name(d, _SEL) == ""

    def test_returns_empty_on_exception(self):
        d = _driver()
        d.select.side_effect = Exception("error")
        assert _extract_brand_name(d, _SEL) == ""


# ─── _extract_js_list ─────────────────────────────────────────────────────────

class TestExtractJsList:
    def test_returns_list_result(self):
        d = _driver()
        d.run_js.return_value = ["img1.jpg", "img2.jpg"]
        assert _extract_js_list(d, "js") == ["img1.jpg", "img2.jpg"]

    def test_returns_empty_when_result_not_list(self):
        d = _driver()
        d.run_js.return_value = "not a list"
        assert _extract_js_list(d, "js") == []

    def test_returns_empty_on_exception(self):
        d = _driver()
        d.run_js.side_effect = Exception("JS error")
        assert _extract_js_list(d, "js") == []


# ─── _extract_js_scalar ───────────────────────────────────────────────────────

class TestExtractJsScalar:
    def test_returns_stripped_string(self):
        d = _driver()
        d.run_js.return_value = "  100  "
        assert _extract_js_scalar(d, "js") == "100"

    def test_returns_empty_when_none(self):
        d = _driver()
        d.run_js.return_value = None
        assert _extract_js_scalar(d, "js") == ""

    def test_returns_empty_on_exception(self):
        d = _driver()
        d.run_js.side_effect = Exception("JS error")
        assert _extract_js_scalar(d, "js") == ""


# ─── _extract_attrs ───────────────────────────────────────────────────────────

class TestExtractAttrs:
    def _td(self, text):
        t = MagicMock()
        t.text = text
        return t

    def test_joins_label_value_pairs(self):
        d = _driver()
        row1 = MagicMock()
        row1.select_all.return_value = [self._td("Couleur"), self._td("Rouge")]
        row2 = MagicMock()
        row2.select_all.return_value = [self._td("Poids"), self._td("1kg")]
        d.select_all.return_value = [row1, row2]
        result = _extract_attrs(d, _SEL)
        assert result == "Couleur=Rouge||Poids=1kg"

    def test_skips_rows_with_fewer_than_2_tds(self):
        d = _driver()
        row = MagicMock()
        row.select_all.return_value = [self._td("Only one")]
        d.select_all.return_value = [row]
        assert _extract_attrs(d, _SEL) == ""

    def test_skips_empty_label_or_value(self):
        d = _driver()
        row = MagicMock()
        row.select_all.return_value = [self._td(""), self._td("Value")]
        d.select_all.return_value = [row]
        assert _extract_attrs(d, _SEL) == ""

    def test_returns_empty_on_exception(self):
        d = _driver()
        d.select_all.side_effect = Exception("error")
        assert _extract_attrs(d, _SEL) == ""


# ─── _extract_ref_fab ─────────────────────────────────────────────────────────

class TestExtractRefFab:
    def test_returns_from_characteristics_row(self):
        d = _driver()
        td = MagicMock()
        td.text = " REF123 "
        ref_row = MagicMock()
        ref_row.select_all.return_value = [td]
        d.select.return_value = ref_row
        assert _extract_ref_fab(d, _SEL, str.strip) == "REF123"

    def test_fallback_to_supplier_ref_selector(self):
        call_count = 0
        def select_side(sel, t):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                row = MagicMock()
                row.select_all.return_value = []
                return row
            return _el(text=" SUP456 ")
        d = _driver()
        d.select.side_effect = select_side
        assert _extract_ref_fab(d, _SEL, str.strip) == "SUP456"

    def test_returns_empty_when_both_fail(self):
        d = _driver()
        d.select.side_effect = Exception("error")
        assert _extract_ref_fab(d, _SEL, str.strip) == ""


# ─── _extract_ean ─────────────────────────────────────────────────────────────

class TestExtractEan:
    def test_returns_ean_text(self):
        d = _driver()
        d.select.return_value = _el(text=" 3414971522378 ")
        assert _extract_ean(d, _SEL, str.strip) == "3414971522378"

    def test_returns_empty_when_no_element(self):
        d = _driver()
        d.select.return_value = None
        assert _extract_ean(d, _SEL, str.strip) == ""

    def test_returns_empty_on_exception(self):
        d = _driver()
        d.select.side_effect = Exception("error")
        assert _extract_ean(d, _SEL, str.strip) == ""


# ─── _extract_cross_sell ─────────────────────────────────────────────────────

class TestExtractCrossSell:
    def test_strips_underscore_suffix(self):
        d = _driver()
        d.run_js.return_value = ["693165_3", "693166"]
        result = _extract_cross_sell(d)
        assert "693165" in result
        assert "693166" in result

    def test_deduplicates_refs(self):
        d = _driver()
        d.run_js.return_value = ["123456", "123456_1"]
        result = _extract_cross_sell(d)
        assert result.count("123456") == 1

    def test_returns_empty_on_exception(self):
        d = _driver()
        d.run_js.side_effect = Exception("error")
        assert _extract_cross_sell(d) == []

    def test_returns_empty_when_non_list(self):
        d = _driver()
        d.run_js.return_value = None
        assert _extract_cross_sell(d) == []


# ─── _get_combo_headers ───────────────────────────────────────────────────────

class TestGetComboHeaders:
    def test_returns_stripped_header_texts(self):
        d = _driver()
        h1, h2 = MagicMock(), MagicMock()
        h1.text = " Référence "
        h2.text = " Couleur "
        d.select_all.return_value = [h1, h2]
        result = _get_combo_headers(d, _SEL)
        assert result == ["Référence", "Couleur"]

    def test_returns_empty_on_exception(self):
        d = _driver()
        d.select_all.side_effect = Exception("error")
        assert _get_combo_headers(d, _SEL) == []


# ─── _set_child_refs ──────────────────────────────────────────────────────────

class TestSetChildRefs:
    def _combo(self, ref_text):
        td = MagicMock()
        td.text = ref_text
        combo = MagicMock()
        combo.select_all.return_value = [td]
        return combo

    def test_sets_child_refs_with_base_ref(self):
        base_row = {"productRef": "100", "childRefs": "100"}
        combos = [self._combo("101"), self._combo("102")]
        _set_child_refs(base_row, combos, str.strip)
        assert base_row["childRefs"] == "100||101||102"

    def test_does_not_duplicate_base_ref(self):
        base_row = {"productRef": "100", "childRefs": "100"}
        combos = [self._combo("100")]
        _set_child_refs(base_row, combos, str.strip)
        assert base_row["childRefs"] == "100"

    def test_empty_base_ref_excluded(self):
        base_row = {"productRef": "", "childRefs": ""}
        combos = [self._combo("101")]
        _set_child_refs(base_row, combos, str.strip)
        assert base_row["childRefs"] == "101"


# ─── _build_combo_row ────────────────────────────────────────────────────────

class TestBuildComboRow:
    def _combo(self, tds_texts):
        tds = []
        for t in tds_texts:
            td = MagicMock()
            td.text = t
            tds.append(td)
        combo = MagicMock()
        combo.select_all.return_value = tds
        return combo

    def test_sets_is_combination_true(self):
        base = {"productRef": "100", "childRefs": "100"}
        combo = self._combo(["101", "Rouge"])
        row = _build_combo_row(base, combo, ["Ref", "Couleur"], "100", str.strip)
        assert row["isCombination"] == "True"

    def test_overrides_product_ref(self):
        base = {"productRef": "100", "childRefs": "100"}
        combo = self._combo(["101", "Bleu"])
        row = _build_combo_row(base, combo, ["Ref", "Couleur"], "100||101", str.strip)
        assert row["productRef"] == "101"

    def test_returns_base_dict_when_no_tds(self):
        base = {"productRef": "100", "childRefs": "100"}
        combo = MagicMock()
        combo.select_all.return_value = []
        row = _build_combo_row(base, combo, [], "100", str.strip)
        assert row["isCombination"] == "True"
        assert row["productRef"] == "100"

    def test_builds_decli_parts(self):
        base = {"productRef": "100", "childRefs": "100"}
        combo = self._combo(["101", "Rouge", "L"])
        row = _build_combo_row(base, combo, ["Ref", "Couleur:", "Taille"], "100||101", str.strip)
        assert "Couleur=Rouge" in row.get("productDecliName&Value", "")
        assert "Taille=L" in row.get("productDecliName&Value", "")

    def test_skips_empty_val(self):
        base = {"productRef": "100", "childRefs": "100"}
        combo = self._combo(["101", "   ", "L"])
        row = _build_combo_row(base, combo, ["Ref", "Couleur", "Taille"], "100", str.strip)
        assert "Couleur" not in row.get("productDecliName&Value", "")
        assert "Taille=L" in row.get("productDecliName&Value", "")


# ─── _build_rows ─────────────────────────────────────────────────────────────

class TestBuildRows:
    def _base(self):
        return {"productRef": "100", "childRefs": "100"}

    def test_returns_base_row_marked_false_when_no_combos(self):
        d = _driver()
        d.select_all.return_value = []
        rows = _build_rows(d, self._base(), _SEL, str.strip)
        assert len(rows) == 1
        assert rows[0]["isCombination"] == "False"

    def test_returns_one_row_per_combo(self):
        d = _driver()
        combo1, combo2 = MagicMock(), MagicMock()
        combo1.select_all.return_value = []
        combo2.select_all.return_value = []

        call_count = 0
        def sel_side(sel, t):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [combo1, combo2]
            return []
        d.select_all.side_effect = sel_side
        rows = _build_rows(d, self._base(), _SEL, str.strip)
        assert len(rows) == 2

    def test_returns_base_row_marked_false_on_exception(self):
        d = _driver()
        d.select_all.side_effect = Exception("DOM crash")
        rows = _build_rows(d, self._base(), _SEL, str.strip)
        assert len(rows) == 1
        assert rows[0]["isCombination"] == "False"

    def test_exception_sets_combination_index_empty(self):
        d = _driver()
        d.select_all.side_effect = Exception("DOM crash")
        rows = _build_rows(d, self._base(), _SEL, str.strip)
        assert rows[0]["combinationIndex"] == ""
