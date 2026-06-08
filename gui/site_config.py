"""
Registre de configuration des sites pour l'interface graphique.

Chaque entrée de SITES_CONFIG décrit :
  - has_categories : afficher ou non le sélecteur de catégorie (produits)
  - categories     : liste importée depuis selectors/<site>.py (source unique)
  - imports        : chemins de modules scrap_*.py à charger dynamiquement

Convention de nommage des modules :
  scrapers.{Site}_P{n}.{type}.scrap_{site}_{type}
  où type ∈ produits | commandes | suivi | suppr
"""

import sys
from pathlib import Path

# Assure que la racine du projet est importable depuis gui/
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from css_selectors.setin import Selectors as _SetinSelectors
from css_selectors.legallais import CATEGORY_NAMES as _LegallaisCategories

SITES_CONFIG = {
    # ─── Setin (fournisseur P5) — www.setin.fr ───────────────────────────────
    "Setin": {
        "has_categories": True,
        "categories": _SetinSelectors.CATEGORY_NAMES,
        "imports": {
            "produits":  "scrapers.Setin_P5.products.scrap_setin_products",
            "commandes": "scrapers.Setin_P5.orders.scrap_setin_orders",
            "suivi":     "scrapers.Setin_P5.tracking.scrap_setin_tracking",
            "suppr":     "scrapers.Setin_P5.deleting.scrap_suppradrr_p5",
        }
    },
    # ─── Prolians (fournisseur P3) — www.prolians.fr ───────────────────────
    "Prolians": {
        "has_categories": False,  # scraping via sitemap, pas de filtre catégorie
        "categories": [],
        "imports": {
            "produits":  "scrapers.Prolians_P3.products.scrap_prolians_products",
            "commandes": "scrapers.Prolians_P3.orders.scrap_prolians_orders",
            "suivi":     "scrapers.Prolians_P3.tracking.scrap_prolians_tracking",
            "suppr":     "scrapers.Prolians_P3.deleting.scrap_suppradrr",
        }
    },
    # ─── Legallais (fournisseur P1) — www.legallais.com ────────────────────
    "Legallais": {
        "has_categories": True,
        "categories": _LegallaisCategories,
        "imports": {
            "produits":  "scrapers.Legallais_P1.products.scrap_legallais_products",
            "commandes": "scrapers.Legallais_P1.orders.scrap_legallais_orders",
            "suivi":     "scrapers.Legallais_P1.tracking.scrap_legallais_tracking",
            "suppr":     "scrapers.Legallais_P1.deleting.scrap_legallais_suppradrr",
        }
    },
}
