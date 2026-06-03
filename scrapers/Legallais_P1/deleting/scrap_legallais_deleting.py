"""Point d'entrée Legallais suppression d'adresses pour l'interface GUI."""

from __future__ import annotations
from pathlib import Path

import css_selectors.legallais as SEL
from gui.scraper_adapter import SubprocessScraper

SCRIPT = Path(__file__).resolve().parent / "scraper_legallais_deleting.py"


class LegallaisDeletingScraper(SubprocessScraper):
    """Wrapper qui exécute le script de suppression d'adresses Legallais."""

    def __init__(self) -> None:
        args = [str(SCRIPT)]
        super().__init__(args, cwd=SCRIPT.parent)


def create_scraper() -> LegallaisDeletingScraper:
    return LegallaisDeletingScraper()

__all__ = ["create_scraper"]
