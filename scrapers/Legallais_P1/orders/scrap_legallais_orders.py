"""Point d'entrée Legallais commandes pour l'interface GUI."""

from __future__ import annotations

try:
    from .scraper_legallais_orders import create_scraper
except ImportError:
    from scrapers.Legallais_P1.orders.scraper_legallais_orders import create_scraper  # type: ignore[no-redef]

__all__ = ["create_scraper"]
