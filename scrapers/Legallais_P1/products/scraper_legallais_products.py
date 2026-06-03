"""Adaptateur scraper Legallais produits qui utilise selectors.legallais."""

from __future__ import annotations
from pathlib import Path

import css_selectors.legallais as SEL
from gui.scraper_adapter import SubprocessScraper

SCRIPT = Path(__file__).resolve().parent / "scraper.py"
ROOT = Path(__file__).resolve().parents[3]


class LegallaisProductScraper(SubprocessScraper):
    """Wrapper qui exécute le script Legallais produits en externe."""

    def __init__(self, category_name: str = "") -> None:
        args = [str(SCRIPT), "--mode", "browse", "--workers", "2"]
        category = (category_name or "Consommables").strip()
        args.extend(["--category", category])
        super().__init__(args, cwd=ROOT)


def create_scraper(category_name: str = "") -> LegallaisProductScraper:
    return LegallaisProductScraper(category_name=category_name)
