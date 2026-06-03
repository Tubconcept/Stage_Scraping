"""Adaptateur scraper Legallais commandes qui utilise selectors.legallais."""

from __future__ import annotations
from pathlib import Path

import css_selectors.legallais as SEL
from gui.scraper_adapter import SubprocessScraper

SCRIPT = Path(__file__).resolve().parent / "cmd.py"


class LegallaisOrderScraper(SubprocessScraper):
    """Wrapper qui exécute le script Legallais commandes en externe."""

    def __init__(self, date_from: str | None = None, date_to: str | None = None) -> None:
        args = [str(SCRIPT)]
        if date_from:
            args.extend(["--date-from", date_from])
        if date_to:
            args.extend(["--date-to", date_to])
        super().__init__(args, cwd=SCRIPT.parent)


def create_scraper(date_from: str | None = None, date_to: str | None = None) -> LegallaisOrderScraper:
    return LegallaisOrderScraper(date_from=date_from, date_to=date_to)
