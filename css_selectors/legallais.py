"""
Sélecteurs CSS et constantes pour Legallais (www.legallais.com — fournisseur P1).

En bas de fichier : dict SELECTORS (plat) et CATEGORY_NAMES pour compatibilité
avec les anciens scripts Botasaurus et cookie_manager_legallais.

Utilisation :
    from css_selectors.legallais import Selectors, SELECTORS, CATEGORY_NAMES
"""


class Selectors:
    """Registre de tous les sélecteurs Legallais — classe statique, ne pas instancier."""

    # --- URLs de base ---
    BASE_URL:  str = "https://www.legallais.com"
    LOGIN_URL: str = "https://www.legallais.com/user/connection"

    # --- Catégories niveau 1 ---
    CATEGORY_NAMES: list[str] = [
        "Bâtiment",
        "Agencement",
        "Plomberie",
        "Génie climatique",
        "Électricité",
        "Consommables",
        "Outillage",
        "Matériels",
        "Protection",
    ]

    # --- Connexion ---
    email    = "input[name='connexion[login]'], input[type='text'], #connection-id"
    password = "input[name='connexion[password]'], input[type='password'], #connection-passwd"
    submit   = "button[data-action='components--connection#sendConnection']"

    # --- Navigation catalogue ---
    breadcrumb       = "ol.c-breadcrumb"
    breadcrumb_items = "ol.c-breadcrumb li"
    menu_products    = ".o-menu__products-items"
    menu_level1   = ".o-menu__level--1 .o-menu__items__list > li > a span.c-menu-item__text"
    menu_level2   = "div.o-menu__level--2 > div.o-menu__items--active ul.o-menu__items__list li > a"
    menu_level3   = ".o-menu__level--3 > .o-menu__items--active > .o-menu__items__list li.o-menu__item a.c-menu-item"

    # --- Listing / pagination ---
    search_input              = "input[type='search']"
    search_result             = "div.c-article-search-card a.c-article-search-card__hover"
    ref_result                = "div.c-article-search-card div.c-article-search-card__inner div.c-article-search-card__info p.c-article-search-card__ref span.c-article-search-card__ref__value"
    product_card              = ".c-article-card.c-article-card--incontainer"
    product_link              = "a.c-article-card__product-info"
    pagination_select         = "select.c-pagination__selector"
    pagination_options        = f"{pagination_select} option"
    pagination_items_per_page = "select.c-pagination__nbrpp__select"

    # ================= page produit (aligné CSV_HEADERS) =================

    product_reference     = "div.c-reference"                              # product_reference_fournisseur
    ean_value             = "span.code_ean span.code_ean_value"             # product_ean
    supplier_ref          = "span.ref_fournisseur span.ref_fournisseur_value"  # product_reference_fabricant
    brand_image           = "a.u-display-contents img"                     # product_brand_logo_url (src) + brand_name (alt)
    product_title         = "div.product-title > h1 > span"                # product_designation
    description           = "section.description"                          # product_description
    product_category_tree = "ol.c-breadcrumb-legacy li.c-breadcrumb-legacy__item"  # product_category_tree
    stock                 = "div.c-stock > div.c-stock__label"             # product_stock_status
    price_eco             = "div.c-price.c-price--rep div.c-price__price"  # product_eco_taxe
    price_original        = ".c-price.c-price--original"                   # product_promotion
    price_final           = "div.c-price.c-price--final .c-price__price"   # product_price_ht (valeur seule)
    characteristics_ref   = "tr#characCodeProvider"                        # product_reference_fabricant (la ligne entière)
    characteristics_table = "#characteristicsTable tr"                     # product_attributes (toutes les lignes)
    combinations_table    = "table#DataTables_Table_0 > tbody > tr"        # product_combination_values
    combinations_headers  = "table#DataTables_Table_0 thead th"
    product_images        = "div.c-photo-gallery__slide img"               # product_image_url (image principale)
    documents             = "a[href*='.pdf']"                              # product_docs_url (PDF uniquement)
    cross_sell            = "div.cross-sell .c-reference, div[class*='associated'] .c-reference, section[class*='related'] .c-reference"  # product_cross_sell

    # =============== commandes =================

    orders_ref_cmd              = "table#DataTables_Table_0 tbody tr"
    orders_date_cmd             = "td.sorting_1"
    orders_statut_cmd           = "span.c-tag"
    orders_data_pdt             = "div.order-details__parcel-designation > div.order-details__parcel-designation-ref"

    # Détail commande
    order_header_lines          = "div.o-wrapper > div.user-content div.order-details__heading > div.pro-space-order-details__container div.pro-space-order-details__line"
    order_date_text             = "p.order-details__heading-date span.u-bold"
    order_product_link          = "div.order-details__parcel-designation > a.order-details__parcel-designation-link"
    order_product_reference     = "div.order-details__parcel-designation > div.order-details__parcel-designation-ref"
    order_product_quantity      = "div.order-details__parcel-quantity-number"
    order_reliquat_block        = "div.order-details__parcel-designation"
    order_reliquat_link         = "a.order-details__parcel-designation-link"
    order_reliquat_stock        = "div.c-stock__label"
    order_multi_container       = "div.c-card div.o-layout__item"
    order_multi_item            = "li.order-details__parcel-item"

    # =============== suivi / tracking =================

    tracking_date_cmd           = "td.sorting_1"
    tracking_statut_cmd         = "span.c-tag"
    tracking_date_reliquat      = "div.c-stock__label"
    tracking_weight             = "table tbody tr.order-details__modal-tracking-info-content td"
    tracking_modal_button       = "a.modal-link"
    tracking_modal              = ".mfp-content"
    tracking_table_cells        = "table tbody tr.order-details__modal-tracking-info-content td"
    tracking_link               = "a"


# ─── Compatibilité avec scraper_bot.py et scraper.py ──────────────────────────
# Dict plat construit depuis la classe — zéro duplication de valeurs.

BASE_URL       = Selectors.BASE_URL
LOGIN_URL      = Selectors.LOGIN_URL
CATEGORY_NAMES = Selectors.CATEGORY_NAMES

SELECTORS: dict[str, str] = {
    # Auth
    "email":                     Selectors.email,
    "password":                  Selectors.password,
    "submit":                    Selectors.submit,
    # Navigation
    "breadcrumb":                Selectors.breadcrumb,
    "breadcrumb_items":          Selectors.breadcrumb_items,
    "menu_level1":               Selectors.menu_level1,
    "menu_level2":               Selectors.menu_level2,
    "menu_level3":               Selectors.menu_level3,
    # Listing / pagination
    "pagination_items_per_page": Selectors.pagination_items_per_page,
    "product_card":              Selectors.product_card,
    "pagination_options":        Selectors.pagination_options,
    "pagination_select":         Selectors.pagination_select,
    "product_link":              Selectors.product_link,
    "search_input":              Selectors.search_input,
    "search_result":             Selectors.search_result,
    "ref_result":                Selectors.ref_result,
    # Page produit
    "product_reference":         Selectors.product_reference,
    "product_title":             Selectors.product_title,
    "price_final":               Selectors.price_final,
    "price_original":            Selectors.price_original,
    "price_eco":                 Selectors.price_eco,
    "stock":                     Selectors.stock,
    "brand_image":               Selectors.brand_image,
    "product_images":            Selectors.product_images,
    "description":               Selectors.description,
    "documents":                 Selectors.documents,
    "characteristics_table":     Selectors.characteristics_table,
    "characteristics_ref":       Selectors.characteristics_ref,
    "combinations_table":        Selectors.combinations_table,
    "combinations_headers":      Selectors.combinations_headers,
    # Nouveaux champs
    "product_ean":               Selectors.ean_value,
    "supplier_ref":              Selectors.supplier_ref,
    "cross_sell":                Selectors.cross_sell,
}
