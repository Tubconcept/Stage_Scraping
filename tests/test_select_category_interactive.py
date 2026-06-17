"""
Tests unitaires pour _select_category_interactive()
dans scrapers/Legallais_P1/products/scrap_legallais_products.py

Couvre la réduction Cognitive Complexity de 18 → ≤6 sur main()
via extraction de _select_category_interactive().
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.Legallais_P1.products.scrap_legallais_products import (
    _select_category_interactive,
)
from css_selectors.legallais import CATEGORY_NAMES


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _run_with_inputs(*inputs):
    """Call _select_category_interactive() with a sequence of mocked input() calls."""
    with patch("builtins.input", side_effect=list(inputs)):
        return _select_category_interactive()


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestSelectCategoryInteractive:
    def test_choice_1_returns_first_category(self):
        result = _run_with_inputs("1")
        assert result == CATEGORY_NAMES[0]

    def test_choice_last_returns_last_category(self):
        n = len(CATEGORY_NAMES)
        result = _run_with_inputs(str(n))
        assert result == CATEGORY_NAMES[n - 1]

    def test_choice_n_plus_1_returns_none(self):
        n = len(CATEGORY_NAMES)
        result = _run_with_inputs(str(n + 1))
        assert result is None

    def test_invalid_string_retries_then_succeeds(self):
        result = _run_with_inputs("abc", "1")
        assert result == CATEGORY_NAMES[0]

    def test_zero_is_invalid_retries(self):
        result = _run_with_inputs("0", "1")
        assert result == CATEGORY_NAMES[0]

    def test_out_of_range_high_retries(self):
        n = len(CATEGORY_NAMES)
        result = _run_with_inputs(str(n + 2), "1")
        assert result == CATEGORY_NAMES[0]

    def test_eoferror_retries(self):
        result = _run_with_inputs(EOFError(), "1")
        assert result == CATEGORY_NAMES[0]

    def test_multiple_invalid_then_all_categories(self):
        n = len(CATEGORY_NAMES)
        result = _run_with_inputs("abc", "0", str(n + 1))
        assert result is None

    def test_prints_category_list(self, capsys):
        _run_with_inputs("1")
        output = capsys.readouterr().out
        assert "Catégories disponibles" in output
        for name in CATEGORY_NAMES:
            assert name in output

    def test_prints_all_categories_option(self, capsys):
        n = len(CATEGORY_NAMES)
        _run_with_inputs(str(n + 1))
        output = capsys.readouterr().out
        assert "Toutes les catégories" in output

    def test_invalid_prints_retry_message(self, capsys):
        _run_with_inputs("999", "1")
        output = capsys.readouterr().out
        assert "Entrez un numéro" in output
