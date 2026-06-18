"""
Sélecteurs CSS et constantes pour le site Sonepar (www.sonepar.fr — fournisseur P8).

Ce fichier est la source unique des sélecteurs DOM pour tous les scrapers Sonepar_P8.
Les noms d'attributs de la section « page produit » sont alignés sur le header CSV
de scrap_p8_PW (productRef, EAN, Ref_fabicant, ...).

Utilisation :
    from css_selectors.sonepar import Selectors
    page.locator(Selectors.menu_mega_button).click()
"""


class Selectors:
    """Registre de tous les sélecteurs Sonepar — classe statique, ne pas instancier."""

    # --- URLs de base ---
    BASE_URL: str = "https://www.sonepar.fr/"

    CATEGORY_NAMES: list[str] = [
        "Fil et câble",
        "Conduit et cheminement",
        "Interrupteur - prise - boîte d encastrement",
        "Distribution d'énergie",
        "Process et contrôle industriel",
        "Eclairage",
        "Sécurité, àccès et communication",
        "Génie climatique",
        "Outillage et accessoires d installation",
        "Service",
        "Production d énergie et E-mobilité",
    ]

    # --- Cookies / consentement ---
    cookie_accept_button = "#onetrust-accept-btn-handler"

    # --- Connexion ---
    login_button = '[data-testid="login-button"]'
    email_input = "input[name='Adresse e-mail']"
    password_input = "input[name='Mot de passe']"
    submit = 'button[type="submit"]'

    # --- Mega-menu / navigation catalogue ---
    menu_mega_button = "#mega-menu-button"
    category_level1_button = "ul.root-panel_list__DCRBg li.root-panel_listItemRoot__2n_H0 button.root-panel_listItemLink__zJrhe"
    mega_panel_content = "#megaPanelContent"
    category_level2_links = "div.mega-panel-item_root__thZC6 > div.mega-panel-item_items__9nhjf a"
    mega_panel_back_button = "div.mega-panel_backButtonDesktop__jFlRN button"

    # --- Fil d'Ariane (breadcrumb) ---
    breadcrumb_list_item = "ol.watts-breadcrumb__list > li"
    breadcrumb_link = "ol.watts-breadcrumb__list > li > a.watts-breadcrumb__list__link"

    # --- Liste de produits / pagination ---
    # Sélecteur basé sur la classe de base (stable même quand le hash Next.js change)
    product_card_link = "a[class*='productLink']"
    pagination_next = "a[data-testid='pagination-next-page']"

    # ================= page produit (aligné header CSV) =================
    product_title = "h1[data-testid='title-manufacturer']" 
    product_brand_block = "div.pdp-manufacture-heading_headingContainer__rVknk > img"

    product_stock_status = "[data-testid='returnable-container']"

    product_active_tag = ".tags_tagsContainer__xf7VT span.watts-chip--warning"

    product_conditionnement = "div.pdp-specification-bloc_root___3sRC ul.pdp-specification-bloc_specifications__70P_7 li p[data-testid='qte-product']"

    # --- Accordéon Références ---
    accordion_reference_button = "section#accordion-reference > h3 > button"
    accordion_reference_content = "section#accordion-reference > div.watts-accordion__content"
    product_ean = "p[data-testid='gtinValue']"
    product_reference_commerciale = "p[data-testid='opcoProductIdValue']"
    product_reference_fabricant = "p[data-testid='manufacturerRefIdValue']"
    product_card_icons_section = "section[data-testid^='product-card-icons-']"
    product_price = "span[data-testid^='main-price-display-']"

    # --- Accordéon Description ---
    accordion_detail_button = "section#accordion-detail > h3 > button"
    product_description = "section#accordion-detail div.pdp-description-block_pdpDescriptionBlock__ePks1"

    # --- Accordéon Documentation ---
    accordion_document_button = "section#accordion-document > h3 > button"
    product_docs_links = "section#accordion-document div[data-testid='document-panel'] a"

    # --- Images produit ---
    product_images_carousel = "[data-testid='images-carousel'] img"

    # --- Déclinaisons / variantes ---
    variant_name_label = "div[data-testid='variant-block-container'] p.watts-body-3"
    variant_picker_link = "a[data-testid='variant-picker']"
    variant_value_selectable = "[data-testid='variant-block-container'] a:not(.picker_picker-state-unselected__Nf60t):not(.picker_picker-state-disabled__wIzy9)"

    # --- Accordéon Caractéristiques techniques ---
    accordion_techspec_button = "#accordion-techSpec > h3 > button"
    product_techspec_item = "#accordion-techSpec div.watts-accordion__content div.pdp-tech-spec-panel_itemContainer__5nqAR"
    product_techspec_label_value = "p"  # .first = nom, .last = valeur (relatif à product_techspec_item)