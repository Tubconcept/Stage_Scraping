# =========================
# AUTH
# =========================

EMAIL_INPUT = "input[name='connexion[login]'], input[type='text'], #connection-id"

PASSWORD_INPUT = "input[name='connexion[password]'], input[type='password'], #connection-passwd"

LOGIN_BUTTON = "button[data-action='components--connection#sendConnection']"


# =========================
# ACCOUNT
# =========================

BREADCRUMB = "ol.c-breadcrumb"


# =========================
# ORDERS TABLE
# =========================

ORDERS_TABLE_ROWS = "table#DataTables_Table_0 tbody tr"

ORDER_DATE_CELL = "td.sorting_1"

NEXT_PAGE_BUTTON = "a#DataTables_Table_0_next"

ORDER_REFERENCE_CELL = 'td[data-label="Référence"]'


# =========================
# ORDER DETAILS
# =========================

ORDER_HEADER_LINES = (
    "div.o-wrapper > div.user-content "
    "div.order-details__heading > "
    "div.pro-space-order-details__container "
    "div.pro-space-order-details__line"
)

ORDER_DATE_TEXT = "p.order-details__heading-date span.u-bold"

PRODUCT_LINK = (
    "div.order-details__parcel-designation > "
    "a.order-details__parcel-designation-link"
)

PRODUCT_REFERENCE = (
    "div.order-details__parcel-designation > "
    "div.order-details__parcel-designation-ref"
)

PRODUCT_QUANTITY = "div.order-details__parcel-quantity-number"

ORDER_STATUS = "span.c-tag"


# =========================
# RELIQUAT
# =========================

RELIQUAT_PRODUCT_BLOCK = "div.order-details__parcel-designation"

RELIQUAT_PRODUCT_LINK = (
    "a.order-details__parcel-designation-link"
)

RELIQUAT_STOCK_LABEL = "div.c-stock__label"


# =========================
# MULTI PRODUCTS
# =========================

MULTI_PRODUCT_CONTAINER = "div.c-card div.o-layout__item"

MULTI_PRODUCT_ITEM = "li.order-details__parcel-item"


# =========================
# TRACKING MODAL
# =========================

TRACKING_MODAL_BUTTON = "a.modal-link"

TRACKING_MODAL = ".mfp-content"

TRACKING_TABLE_CELLS = (
    "table tbody tr.order-details__modal-tracking-info-content td"
)

TRACKING_LINK = "a"


"""
Configuration centralisée pour le scraper Legallais
"""
from pathlib import Path

# === CONFIGURATION DE BASE ===
BASE_URL = "https://www.legallais.com"
SITE_NAME = "Legallais"

# === CHEMINS ===
BASE_DIR = Path(__file__).parent.parent
CSV_DIR = BASE_DIR / "csv"
LOG_DIR = BASE_DIR / "log"
JSON_DIR = BASE_DIR / "json"
PROFILE_DIR = BASE_DIR / "PlaywrightProfile"
DB_PATH = BASE_DIR / "db" / "products.sqlite"

# Créer les répertoires s'ils n'existent pas
for directory in [CSV_DIR, LOG_DIR, JSON_DIR, PROFILE_DIR, DB_PATH.parent]:
    directory.mkdir(exist_ok=True, parents=True)

# === CONFIGURATION NAVIGATEUR ===
BROWSER_CONFIG = {
    "user_data_dir": str(PROFILE_DIR),
    "channel": "chrome",
    "headless": False,
}
LOGIN_URL = "https://www.legallais.com/user/connection"
VIEWPORT = {"width": 1920, "height": 1080}

# === TIMEOUTS (en ms) ===
TIMEOUT_PAGE_LOAD = 30000
TIMEOUT_ELEMENT = 5000
TIMEOUT_SHORT = 1000
TIMEOUT_MEDIUM = 3000
TIMEOUT_LONG = 7000

# === SÉLECTEURS CSS ===
SELECTORS = {
    # Menu
    "menu_products": ".o-menu__products-items",
    "menu_level1": ".o-menu__level--1 .o-menu__items__list > li > a span.c-menu-item__text",
    "menu_level2": "div.o-menu__level--2 > div.o-menu__items--active ul.o-menu__items__list li > a",
    "menu_level3": ".o-menu__level--3 > .o-menu__items--active > .o-menu__items__list li.o-menu__item a.c-menu-item",
    
    # Produits
    "product_card": ".c-article-card.c-article-card--incontainer",
    "product_link": "a.c-article-card__product-info",
    "product_title": "div.product-title > h1 > span",
    "product_reference": "div.c-reference",
    "product_reference_alt": "div.c-buy-box",
    
    # Prix
    "price_final": "div.c-price.c-price--final",
    "price_original": ".c-price.c-price--original",
    "price_eco": "div.c-price.c-price--rep div.c-price__price",
    
    # Disponibilité
    "stock": "div.c-stock > div.c-stock__label",
    
    # Images
    "brand_image": "a.u-display-contents img",
    "product_images": "div.c-photo-gallery__thumbnail-item img",
    
    # Description
    "description": "section.description",
    
    # Docs
    "documents": "div.o-layout.u-padding-horizontal a",
    
    # Caractéristiques
    "characteristics_table": "#characteristicsTable > tbody > tr",
    "characteristics_ref": "tr#characCodeProvider td",
    
    # Déclinaisons
    "combinations_table": "table#DataTables_Table_0 > tbody > tr",
    "combinations_headers": "table#DataTables_Table_0 thead th",
    
    # Breadcrumb
    "breadcrumb": "ol.c-breadcrumb-legacy li.c-breadcrumb-legacy__item",
    
    # Pagination
    "pagination_select": "select.c-pagination__selector",
    "pagination_items_per_page": "select.c-pagination__nbrpp__select",
    "pagination_options": "select.c-pagination__selector > option",
    
    # Recherche
    "search_input": "input[type='search']",
    "search_result": "div.c-article-search-card a.c-article-search-card__hover",
    "ref_result":"div.c-article-search-card div.c-article-search-card__inner div.c-article-search-card__info p.c-article-search-card__ref span.c-article-search-card__ref__value",
    # Connexion
    "email": "input[name='connexion[login]'], input[type='text'], #connection-id",
    "password": "input[name='connexion[password]'], input[type='password'], #connection-passwd",
    "submit": "button[data-action='components--connection#sendConnection']",


    #  actions
    "confirm_modal": "button#delete-account-address-popup-submit",
}