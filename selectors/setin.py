
class Selectors:
    """Centralise tous les sélecteurs CSS pour le site Setin."""
    
    # ========== CONNEXION ==========
    ACCOUNT_LINK = "div#picto-compte-header a" #150
    EMAIL_INPUT = "Votre email" #151
    PASSWORD_INPUT = "Mot de passe" #152
    LOGIN_BUTTON = "form[name='form_compte'] a.jqBtnConnection" #153
    # USER_INFO selector removed (not used in scrapers)
    RETURN_BUTTON = "div.retourBouton a" #535

    # ========== CATÉGORIES ==========
    MENU_PRODUCTS = "li#menu-produits a.boutonHautLien.d-block" #160
    CATEGORY_LEVEL_1 = "ul.category_niv1 li[aria-label=\"{}\"] a" #161
    CATEGORY_LEVEL_2 = "ul.category_niv2.active a" #167
    CATEGORY_LEVEL_3 = "ul.category_niv3.active li:not(.voir_tout_souscategorie) a" #172
    
    # ========== BREADCRUMB (ARIANE) ==========
    BREADCRUMB_CONTAINER = "div.fil_ariane_fond .ariane-thematique-link" # #191

    # ========== PAGINATION ==========
    PAGINATION_BUTTON = "button.jq-pagination" #415

    # ========== PRODUITS LISTING ==========
    PRODUCT_BOX = "div.product_box" #433
    PRODUCT_BOX_IMAGE_LINK = "div.product_box div.bp_image a" #434

    # ========== TABLEAU DE VARIATIONS ==========
    TABLE_CONTAINER = "div#fiche_article_annexe div.hide-for-small-only div#tableau-var" #459
    TABLE_ROW = "div.ligne_tableau" #460
    TABLE_ROW_OPENED_CLASS = "ligne_ouverte" #470
    DETAIL_BUTTON = "a.bouton_detail_var" #471
    
    # ========== TITRE & RÉFÉRENCE ==========
    TITLE_VARIATION = "div.titre_variation" #234
    REF_VARIATION = "div.variante_ref span.ref_var" #240
    
    # ========== PRIX & TAXES ==========
    PRICE_CONTAINER = "div.prix_unitaire" #251
    PRICE_VAR = "div.prix_unitaire span.prix_var" #251
    PRICE_REDUCED = "div.prix_unitaire span.barrer_prix:not(.hide) span" #258
    ECO_TAX = "div.prix_unitaire span.tableau_var_eco_taxe.fa_ecotaxe span" #267

    # ========== IMAGE PRODUIT ==========
    PRODUCT_IMAGE = "div.photo_variante img" #274

    # ========== MARQUE ==========
    BRAND_CONTAINER = "div#fiche_article_head div.entete_marque img" #280
    
    # ========== DOCUMENTS ==========
    DOCUMENT_LINKS = "div.entete-download a" #288

    # ========== CONDITIONNEMENT ==========
    CONDITIONNEMENT_SELECTORS = [
        "div.loaded_quantity div.without_suremballage:not(.hide) div.no-padding a.active", #211
        "div.loaded_quantity div.without_suremballage:not(.hide) div.no-padding", #212 
        "div.loaded_quantity div.with_suremballage:not(.hide) div.no-padding a.active", #213
        "div.loaded_quantity div.with_suremballage:not(.hide) div.no-padding", #214
    ]

    # ========== DÉTAILS PRODUIT ==========
    DETAIL_CONTAINER = "div.detail_var_{row_id}:not(.id_var_{row_id})" #311
    EAN_CODE = "span.code_ean span.code_ean_value" #313
    SUPPLIER_REF = "span.ref_fournisseur span.ref_fournisseur_value" #314

    # ========== DESCRIPTIONS ==========
    ARTICLE_DESCRIPTION_SHORT = "div.article_description_courte" #320
    VARIANT_DESCRIPTION_SHORT = "div.variante_description_courte" #323
    DESCRIPTION_LONG = "div#div_description_longue" #329
    CHARACTERISTIC = "div#div_description_longue div.carac" #330

    # ========== DÉCLINAISONS ==========
    COMBINATION_DESCRIPTION = "div.description2 div.carac" #343

    # ========== GESTION ADRESSES (suppr_Addr.py) ==========
    ADDRESS_LIST_CONTAINER = "div#jqListAdresse div.itemAddress" #57
    ADDRESS_BUTTON_SHRINK = "div.medium-shrink" #58