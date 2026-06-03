"""Adaptateur scraper Legallais suivi qui utilise selectors.legallais."""

from __future__ import annotations
from pathlib import Path

import css_selectors.legallais as SEL
from gui.scraper_adapter import SubprocessScraper

SCRIPT = Path(__file__).resolve().parent / "tracking.py"


class LegallaisTrackingScraper(SubprocessScraper):
    """Wrapper qui exécute le script Legallais suivi en externe."""

    def __init__(self) -> None:
        args = [str(SCRIPT)]
        super().__init__(args, cwd=SCRIPT.parent)


def create_scraper() -> LegallaisTrackingScraper:
    return LegallaisTrackingScraper()
