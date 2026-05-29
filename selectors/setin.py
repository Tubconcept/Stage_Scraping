"""Sélecteurs CSS/Playwright et constantes pour le site Setin (www.setin.fr)."""

BASE_URL: str = "https://www.setin.fr/"

CATEGORY_NAMES: list[str] = [
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
    "Promium - Une marque du groupe Setin",
]

LOGIN: dict[str, str] = {
    "home_return_button": "div.retourBouton a",
    "account_icon": "div#picto-compte-header a",
    "email_placeholder": "Votre email",
    "password_placeholder": "Mot de passe",
    "submit": "form[name='form_compte'] a.jqBtnConnection",
    # Présent uniquement si l'utilisateur est connecté — utilisé pour détecter la session active
    "user_info": "div.info-perso",
    "page_loader": "div#loaderPage",
}

NAVIGATION: dict[str, str] = {
    "menu_products": "li#menu-produits a.boutonHautLien.d-block",
    "category_level1": 'ul.category_niv1 li[aria-label="{category}"] a',
    "category_level2": "ul.category_niv2.active a",
    "category_level3": "ul.category_niv3.active li:not(.voir_tout_souscategorie) a",
    "breadcrumb": "div.fil_ariane_fond .ariane-thematique-link",
    "pagination_next": "button.jq-pagination",
    "product_box_link": "div.product_box div.bp_image a",
}

ORDERS: dict[str, str] = {
    # URL du backoffice commandes client
    "orders_url": "dhtml/commande_setin.php",

    # Liste des commandes
    "order_row": "div.row.commande",
    "order_link": "div.listing-setin-ligne-1 div.listing-setin-label a",
    "order_date": "div.listing-setin-ligne-1 div.listing-setin-valeur",
    "order_status": "div.listing-setin-ligne-2 div.listing-setin-valeur font",
    "order_tracking": "div.listing-setin-ligne-3 span.listing-setin-valeur a",
    "order_reliquat": "div.listing-setin-ligne-4 div.listing-setin-valeur",   # TODO: vérifier sur la page
    # ID dynamique pour la réf interne : f"div#ordernum_BXXXXX"
    "order_ref_template": "div#ordernum_{ref_px}",

    # Pagination commandes (Oasis — changeOrders(), pas jq-pagination produits)
    "orders_pagination": ".oasis-pagination",
    "pagination_next": "a.pagination-next.chevron-pagination:not(.disable)",
    "pagination_current": ".actual-pagination.highlight",

    # Page détail produit de la commande
    "product_articles": ".listing-setin-articles",
    "product_label": "div.row div.listing-setin-label",
    "product_text": "div.row div.listing-setin-texte",
    "product_value": "div.row div.listing-setin-valeur",

    # Champs supplémentaires sur la page détail (suivi)
    # TODO: vérifier ces sélecteurs sur la page de détail commande
    "detail_weight": "span.poids-expedition",
    "detail_reliquat": "span.date-reliquat",
}

PRODUCT_DETAIL: dict = {
    "product_table": "div#fiche_article_annexe div.hide-for-small-only div#tableau-var",
    "product_row": "div.ligne_tableau",
    "row_detail_button": "a.bouton_detail_var",
    "row_title": "div.titre_variation",
    "row_ref": "div.variante_ref span.ref_var",
    "row_image": "div.photo_variante img",
    "unit_price": "div.prix_unitaire span.prix_var",
    "strikethrough_price": "div.prix_unitaire span.barrer_prix:not(.hide) span",
    "eco_tax": "div.prix_unitaire span.tableau_var_eco_taxe.fa_ecotaxe span",
    "brand_image": "div#fiche_article_head div.entete_marque img",
    "doc_links": "div.entete-download a",
    "packaging_selectors": [
        "div.loaded_quantity div.without_suremballage:not(.hide) div.no-padding a.active",
        "div.loaded_quantity div.without_suremballage:not(.hide) div.no-padding",
        "div.loaded_quantity div.with_suremballage:not(.hide) div.no-padding a.active",
        "div.loaded_quantity div.with_suremballage:not(.hide) div.no-padding",
    ],
    "detail_panel_template": "div.detail_var_{row_id}:not(.id_var_{row_id})",
    "ean_value": "span.code_ean span.code_ean_value",
    "supplier_ref": "span.ref_fournisseur span.ref_fournisseur_value",
    "short_desc_article": "div.article_description_courte",
    "short_desc_variant": "div.variante_description_courte",
    "long_description": "div#div_description_longue",
    "characteristic_row": "div.carac",
    "variation_characteristics": "div.description2 div.carac",
    "table_wait": "div#tableau-var",
    "stock_status": "div.mise_en_avant_stock span.texte_stock span.texte",
    "quantity_input": "input[name='quantite']",
}
