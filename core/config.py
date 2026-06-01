from pathlib import Path
import sys

# === COLONNES CSV ===
CSV_HEADERS = [
    "product_fournisseur",
    "product_reference_fournisseur",
    "product_ean",
    "product_reference_fabricant",
    "product_brand",
    "product_brand_logo_url",
    "product_designation",
    "product_description",
    "product_image_url",
    "product_docs_url",
    "product_category_tree",
    "product_conditionnement",
    "product_stock_status",
    "product_status",
    "product_fournisseur_url",
    "product_eco_label",
    "product_eco_taxe",
    "product_promotion",
    "product_price_ht",
    "product_attributes",
    "products_is_combination",
    "product_combination_index",
    "product_parent_reference",
    "product_child_reference",
    "product_combination_values",
    "product_cross_sell",
]

# === TEXTE À IGNORER DANS LES LOGS ===
IGNORED_ERRORS = [
    "Target page, context or browser has been closed",
    "Browser has been closed",
    "TargetClosedError",
]

# === TIMEOUTS (en ms) ===
TIMEOUT_PAGE_LOAD = 30000
TIMEOUT_ELEMENT = 5000
TIMEOUT_SHORT = 1000
TIMEOUT_MEDIUM = 3000
TIMEOUT_LONG = 7000

# === TIMEOUTS (en secondes) ===
TIMEOUT_LONG_SEC = TIMEOUT_LONG // 1000
TIMEOUT_MEDIUM_SEC = TIMEOUT_MEDIUM // 1000
TIMEOUT_SHORT_SEC = TIMEOUT_SHORT // 1000
TIMEOUT_ELEMENT_SEC = TIMEOUT_ELEMENT // 1000
TIMEOUT_PAGE_LOAD_SEC = TIMEOUT_PAGE_LOAD // 1000


# === PAGINATION ===
# Always resolve to project root (go up 2 levels from core/ to reach project root)
DIRECTORY = Path(__file__).resolve().parent.parent
LOG_DIR = DIRECTORY / "log"
CSV_DIR = DIRECTORY / "csv"
JSON_DIR = DIRECTORY / "json"
PROFILES_DIR = DIRECTORY / "playwright_profiles"
DB_PATH = DIRECTORY / "setin_data.db"


VIEWPORT = {"width": 1920, "height": 1080}