
from pathlib import Path
import sys

# === COLONNES CSV ===
CSV_HEADERS = [
    "productRef",
    "EAN",
    "Ref_fabicant",
    "cat1",
    "cat2",
    "cat3",
    "productTitle",
    "conditionnement",
    "productAttributes",
    "productBrand",
    "Image Brand",
    "productDesc",
    "productDocList",
    "productImages",
    "combinationIndex",
    "productCombinationNames",
    "productCombinationValues",
    "isCombination",
    "Parent",
    "productPrice",
    "Eco_Tax",
    "Reduction",
    "Produit liée",
    "stockStatus",
    "CategoryTree",
    "Declinaison",
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
DIRECTORY= Path(sys.argv[0]).resolve().parent
LOG_DIR = DIRECTORY / "log"
CSV_DIR = DIRECTORY / "csv"
JSON_DIR = DIRECTORY / "json"


VIEWPORT = {"width": 1920, "height": 1080}