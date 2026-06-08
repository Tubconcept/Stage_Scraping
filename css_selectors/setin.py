"""
Sélecteurs CSS et constantes pour le site Setin (www.setin.fr — fournisseur P5).

Ce fichier est la source unique des sélecteurs DOM pour tous les scrapers Setin_P5.
Les noms d'attributs de la section « page produit » sont alignés sur CSV_HEADERS.

Utilisation :
    from css_selectors.setin import Selectors
    page.locator(Selectors.menu_products).click()
"""


class Selectors:
    """Registre de tous les sélecteurs Setin — classe statique, ne pas instancier."""

    # --- URLs de base ---
    BASE_URL: str = "https://www.setin.fr/"
    ORDERS_URL: str = f"{BASE_URL}dhtml/commande_setin.php"
    ADDRESSES_URL: str = f"{BASE_URL}dhtml/change_adresse_setin.php"

    CATEGORY_NAMES: list[str] = [
        "Promium - Une marque du groupe Setin",
        "Equipement de la porte, fenêtre et portail",
        "Verrouillage et sécurité",
        "Quincaillerie générale de bâtiment",
        "Quincaillerie d'agencement et d'ameublement",
        "Consommables",
        "Outillage à main",
        "Outils de coupe",
        "Hygiène Sécurité Protection",
        "Equipements d'atelier et de chantier",
        "Electricité",
        "Plomberie",
    ]

    # --- Connexion ---
    home_return_button = "div.retourBouton a"
    account_icon = "div#picto-compte-header a"
    email_placeholder = "Votre email"
    password_placeholder = "Mot de passe"
    submit = "form[name='form_compte'] a.jqBtnConnection"
    user_info = "div.info-perso"
    page_loader = "div#loaderPage"

    # --- Navigation catalogue ---
    menu_products = "li#menu-produits a.boutonHautLien.d-block"
    category_level1 = 'ul.category_niv1 li[aria-label="{category}"] a'
    category_level2 = "ul.category_niv2.active a"
    category_level3 = "ul.category_niv3.active li:not(.voir_tout_souscategorie) a"
    breadcrumb = "div.fil_ariane_fond .ariane-thematique-link"
    pagination_next = "button.jq-pagination"
    product_box_link = "div.product_box div.bp_image a"

    # ================= page produit (aligné CSV_HEADERS) =================
    product_table = "div#fiche_article_annexe div.hide-for-small-only div#tableau-var"
    product_row = "div.ligne_tableau"
    row_detail_button = "a.bouton_detail_var"
    product_designation = "div.titre_variation"
    product_reference_fournisseur = "div.variante_ref span.ref_var"
    product_image_url = "div.photo_variante img"
    product_price_ht = "div.prix_unitaire span.prix_var"
    product_promotion = "div.prix_unitaire span.barrer_prix:not(.hide) span"
    product_eco_taxe = "div.prix_unitaire span.tableau_var_eco_taxe.fa_ecotaxe span"
    product_brand = "div#fiche_article_head div.entete_marque img"
    product_brand_logo_url = "div#fiche_article_head div.entete_marque img"
    product_docs_url = "div.entete-download a"
    product_conditionnement = [
        "div.loaded_quantity div.without_suremballage:not(.hide) div.no-padding a.active",
        "div.loaded_quantity div.without_suremballage:not(.hide) div.no-padding",
        "div.loaded_quantity div.with_suremballage:not(.hide) div.no-padding a.active",
        "div.loaded_quantity div.with_suremballage:not(.hide) div.no-padding",
    ]
    product_stock_status = "div.mise_en_avant_stock span.texte"
    product_stock_status_alt = "div.mise_en_avant_stock, span.texte_stock, span.texte"
    product_status = "div.fin_de_vie.hide-for-small-only"
    product_ean = "span.code_ean span.code_ean_value"
    product_reference_fabricant = "span.ref_fournisseur span.ref_fournisseur_value"
    product_description_article = "div.article_description_courte"
    product_description_variant = "div.variante_description_courte"
    product_category_tree = "div.fil_ariane_fond .ariane-thematique-link"
    product_attributes_block = "div#div_description_longue"
    product_attributes_row = "div.carac"
    products_is_combination = "div.description2 div.carac"
    product_combination_values = "div.description2 div.carac"
    product_cross_sell = "div.carousel-related-products-name span a"
    product_eco_label = (
        'div.entete_marque img[alt*="eco" i], '
        'div.entete_marque img[title*="eco" i], '
        'img[src*="ecolabel"], img[src*="eco-label"]'
    )
    detail_panel_template = "div.detail_var_{row_id}:not(.id_var_{row_id})"
    table_wait = "div#tableau-var"
    quantity_input = "input.quantite_var, input[name*='quantite'], input.qty"

    # =============== commandes / suivi =================
    order_row = "div.row.commande"
    order_link = "div.listing-setin-ligne-1 div.listing-setin-label a"
    order_date = "div.listing-setin-ligne-1 div.listing-setin-valeur"
    order_status = "div.listing-setin-ligne-2 div.listing-setin-valeur font"
    order_tracking = "div.listing-setin-ligne-3 span.listing-setin-valeur a"
    order_reliquat = "div.listing-setin-ligne-4 div.listing-setin-valeur"
    order_ref_template = "div#ordernum_{ref_px}"
    orders_pagination = ".oasis-pagination"
    orders_pagination_next = "a.pagination-next.chevron-pagination:not(.disable)"
    orders_pagination_current = ".actual-pagination.highlight"
    order_product_articles = ".listing-setin-articles"
    order_product_label = "div.row div.listing-setin-label"
    order_product_text = "div.row div.listing-setin-texte"
    order_product_value = "div.row div.listing-setin-valeur"
    detail_weight = "span.poids-expedition, div.poids-expedition, .poids-expedition, span[class*='poids'], div[class*='poids']"
    detail_reliquat = "span.date-reliquat"

    # =============== suppression adresses =================
    address_card = "div.itemAddress"
    address_delete = "a[href*='action=supprimer']"
    # La confirmation est un window.confirm() natif — géré via page.once("dialog")
