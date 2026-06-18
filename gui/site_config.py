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
from css_selectors.sonepar import Selectors as _SoneparSelectors
from css_selectors.sider import Selectors as _SiderSelectors
from css_selectors.prolians import Selectors as _ProliansSelectors

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
    # Produits : navigation par catégorie (marche des URLs /nos-produits).
    # Catégorie vide = catalogue complet. Le scraper sitemap historique reste
    # disponible en CLI : scrapers.Prolians_P3.products.scrap_prolians_products
    "Prolians": {
        "has_categories": True,
        "categories": _ProliansSelectors.CATEGORY_NAMES,
        "imports": {
            "produits":  "scrapers.Prolians_P3.products.scrap_prolians_by_category",
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
    # ─── Sonepar (fournisseur P8) — www.sonepar.fr ────────────────────────
    "Sonepar": {
        "has_categories": True,
        "categories": _SoneparSelectors.CATEGORY_NAMES,
        "imports": {
            "produits":  "scrapers.Sonepar_P8.products.scrap_sonepar_products",
            "refs":      "scrapers.Sonepar_P8.products.scrap_sonepar_by_refs",
            "commandes": None,  # à implémenter
            "suivi":     None,  # à implémenter
            "suppr":     None,  # à implémenter
        }
    },
    # ─── Sider (fournisseur P6) — www.sider.biz ─────────────────────────────
    "Sider": {
        "has_categories": True,
        "categories": _SiderSelectors.CATEGORY_NAMES,
        "imports": {
            "produits": "scrapers.Sider_P6.products.scrap_sider_products",
        }
    },
}
