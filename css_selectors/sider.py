"""
Sélecteurs CSS et constantes pour le site Sider (www.sider.biz — fournisseur P6).

Ce fichier est la source unique des sélecteurs DOM pour tous les scrapers Sider_P6.
Les noms d'attributs de la section « page produit » sont alignés sur CSV_HEADERS.

Utilisation :
    from css_selectors.sider import Selectors
    page.locator(Selectors.product_designation).inner_text()
"""


class Selectors:
    """Registre de tous les sélecteurs Sider — classe statique, ne pas instancier."""

    # --- URLs de base ---
    BASE_URL: str = "https://www.sider.biz/"

    # Catégories de premier niveau du megamenu (texte exact affiché dans le menu).
    # "Toutes les catégories" = pas de filtre, tout le catalogue est parcouru.
    CATEGORY_NAMES: list[str] = [
        "Toutes les catégories",
        "SANITAIRE",
        "PLOMBERIE",
        "Chauffage - Ventilation",
        "Menuiserie - Serrurerie",
        "ÉLECTRICITÉ",
        "DROGUERIE - QUINCAILLERIE",
        "AMEUBLEMENT",
        "ESPACES VERTS",
        "OUTILLAGE",
        "ÉQUIPEMENT",
    ]

    # --- Consentement cookies ---
    cookie_accept = "button#didomi-notice-agree-button"

    # --- Connexion ---
    account_icon   = "a[title='Création de compte/Connexion'] div.login_user"
    email_input    = "#_email"
    password_input = "#_password"
    submit         = "#login"

    # --- Navigation catalogue (découverte dynamique) ---
    all_categories_link = "a.all-categories"
    menu_level1         = "nav.mega-menu > ul > li > a"
    menu_level2         = "ul.level-2-container a.level-2-link"

    # --- Pagination ---
    pagination      = "ul.pagination"
    pagination_next = "a[rel='next']"

    # --- Listing produits (page catégorie) ---
    product_card = "span.product__name"
    product_link = "span.product__name a.js-with-tracking"

    # --- Fil d'Ariane ---
    breadcrumb       = "ol.breadcrumb"
    breadcrumb_items = "ol.breadcrumb h6"   # Index 0 ignoré (= racine / « Accueil »)

    # ================= page produit (aligné CSV_HEADERS) =================

    product_designation           = "div.product-naming h1"

    # Lire l'attribut data-code (pas inner_text) — toujours sur .first
    product_reference_fournisseur = "span.article-code"

    # Prix — voir aussi product_price_actuel / product_price_barre pour la logique promo
    product_price_ht              = "span.price"

    product_description           = "div.product-long-descriptif"

    # Statut stock textuel — valeur brute affichée dans span.stock
    product_stock_status          = "span.stock"

    # Disponibilité visuelle — pastille dans td.article-disponibility
    # Classe "green" présente → "1" (en stock) ; absente → "0"
    product_status                = "td.article-disponibility span.pastille"

    # Même sélecteur, deux attributs : alt → product_brand, src → product_brand_logo_url
    product_brand                 = "div.block-product-media img.brand-product-media"
    product_brand_logo_url        = "div.block-product-media img.brand-product-media"

    # Images produit — lire l'attribut src sur chaque élément
    product_image_url             = "div#thumb-photos img.media-object"

    # Documents techniques — lire l'attribut href sur chaque élément
    product_docs_url              = "div.media div.media-text a"

    # Tableau des caractéristiques : clé = td:nth(0), valeur = td:nth(1)
    product_attributes_table      = "div#tab-caracteristiques tr"

    # Référence fabricant — dans le tableau caractéristiques, ligne « Réf. fabricant »
    product_reference_fabricant   = "table.table-hover tr:has(td:text-is('Réf. fabricant')) td:nth-child(2)"

    # Conditionnement — dans le tableau caractéristiques, ligne « Vendu par »
    # Attention : la valeur contient des espaces parasites → toujours .strip() à l'extraction
    product_conditionnement       = "div#tab-caracteristiques table.table-hover tr:has(td:text-is('Vendu par')) td:nth-child(2)"

    # Éco-participation — une cellule par ligne de déclinaison dans le tableau articles
    # Valeur possible : "0,05 €" (avec &nbsp;) ou "-" si absente → traiter le cas "-"
    product_eco_taxe              = "table.articles-table td.article-ecotax"

    # Pictos produit (Ecolabel, Fabrication France, Engagé…)
    # Matching fait en Python sur l'attribut alt (insensible à la casse) :
    #   pictos = page.locator(Selectors.product_eco_label).all()
    #   eco_label = any("ecolabel" in (img.get_attribute("alt") or "").lower() for img in pictos)
    product_eco_label             = "div.block-product-pictos img"

    # product_ean       → n'existe pas sur Sider, ignoré
    # product_cross_sell → n'existe pas sur Sider, ignoré

    # ================= logique promotions =================
    # Détecter une promo : tester la présence de product_promo_block
    # Si absent → product_price_ht = product_price_actuel, product_promotion = None
    # Si présent → product_price_ht = product_price_barre, product_promotion = product_price_actuel

    product_promo_block    = "div.container-price div.previous-price"
    product_promo_percent  = "div.container-price div.previous-price span.destockage-sm"
    product_price_barre    = "div.container-price div.previous-price del"    # Prix de référence (barré)
    product_price_actuel   = "div.container-price span.price"               # Prix réellement affiché

    # ================= déclinaisons / combinaisons =================

    # Table principale listant toutes les déclinaisons d'un produit
    combinations_table    = "table.articles-table tbody"
    # Ligne individuelle — locator relatif à combinations_table
    combinations_row      = "tr"
    # Bouton de navigation vers la page de la variante : lire data-src, préfixer avec BASE_URL
    combinations_row_link = "button.btn-link"
    # Codes de toutes les variantes du groupe (all_inner_texts sur la table)
    combinations_codes    = "td.article_id span span.article-code"

    # Sur la page d'une variante individuelle :
    # Ligne active dans le tableau récapitulatif
    combination_selected_row      = "tr.article.selected"
    # Axes de déclinaison : data-label = nom de l'axe, inner_text = valeur de la variante
    product_combination_attribute = "td.article-attribute"
    # Toutes les refs du groupe visibles sur la page variante (all_inner_texts)
    article_codes_all             = "span.article-code"
