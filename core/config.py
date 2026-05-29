from pathlib import Path
import sys

# === COLONNES CSV_Produits ===
CSV_HEADERS = [
    "productRef",
    "EAN",
    "Ref_fabricant",
    "Ref_fabricant",
    "cat1",
    "cat2",
    "cat3",
    "productTitle",
    "conditionnement",
    "productAttributes",
    "productBrand",
    "Image_Brand",
    "productDesc",
    "productDocList",
    "productImages",
    "combinationIndex",
    "productDecliName&Value",
    "isCombination",
    "Parent",
    "Ref_Decli",
    "productPrice",
    "Eco_Tax",
    "Reduction",
    "Produit_liee",
    "stockStatus",
    "CategoryTree",
    "ProductUrl",
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
PROFILES_DIR = DIRECTORY.parent / "playwright_profiles"
DB_PATH = DIRECTORY.parent / "setin_data.db"

VIEWPORT = {"width": 1920, "height": 1080}