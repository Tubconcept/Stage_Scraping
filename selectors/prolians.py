"""Sélécteurs CSS et URLS pour le site prolians"""

class Selectors: 
    #--- URLs de base ---
    BASE_URL: str = "https://www.prolians.fr"
    SITEMAP_INDEX = f"{BASE_URL}/sitemap.xml"
    LOGIN_URL: str = f"{BASE_URL}/login"


    # --- Formulaire de connexion 
    email_input         = "input[type='email']"
    email_button        = "button[aria-label='Connexion / Inscription']"
    password_input      = "input[aria-label='Mot de passe']"
    submit_button       = "button#next"
    logged_in_check     = "button[aria-label='Mon compte']"
    logged_out_check    = "a[aria-label='Connectez-vous']"

    # --- navigation ---
    cookie_acceptt       = "button[aria-label*='Accepter']"
    breadcrumb           = '[data-testid="breadcrumb/desktop"] a'


    # ================= page de détail produit ===================
    # Contient : Code P (productRef), EAN, Réf. fabricant, Réf. PROLIANS
    inline_list_item    = "div[data-testid='inline-list-item']"
    title               = 'h1[data-testid="title"]'
    conditionnement     = "span.text-sm.font-medium.text-brand-2-800"
    attributes_row      = '[data-testid="simple-table-row"]'
    brand_name          = 'a[href^="/brand/"] h2[data-testid="title"]'
    brand_image         = '[data-testid="product-cloudflare-picture/image"]'
    description_button  = 'button[data-testid="disclosure-button"]'
    description_content = '.legacy-cms-styles'
    documents           = '[data-testid="document-tag-link"]'
    image_swiper        = '.swiper-wrapper .swiper-slide img'
    image_fallback      = '[data-testid="product-cloudflare-picture/image"]'
    price               = '[data-testid="product-price/price"] span'
    price_message       = '[data-testid="product-price-message"]'
    eco_tax             = '[data-testid="product-price/eco-participation"]'
    reduction           = '[data-testid="product-price/discount"]'
    combinations        = '[role="radiogroup"] input'

    # =============== commandes =================
    # authentification
    cookie_accept       = "button[aria-label='Accepter & Fermer: Accepter notre traitement des données et fermer']"
    didomi_accept       = "#didomi-notice-agree-button"
    accept_all_xpath    = "//button[contains(., 'Tout accepter')]"

    # navigation commandes
    view_all_orders     = "a[aria-label='Voir toutes mes commandes']"
    order_row           = "tr[data-key^='WP-']"
    order_webref        = "td[id$='-webRef']"
    order_internalref   = "td[id$='-internalRef']" 
    order_date          = "td[id$='-orderDate']" 
    order_status        = "td[id$='-status']" 
    next_page           = "button[aria-label='Page suivante']" 
    tracking_button     = "a[aria-label='Suivre ma commande']" 

    # Détail commande (XPath)
    client_order_ref_xpath = "//dt[contains(., 'N° commande client')]/following-sibling::dd[1]"
    product_name           = 'span[data-testid="order-item/product-name"]' 
    product_ref_xpath      = "//span[contains(., 'Réf. PROLIANS :')]" 
    prodcut_qty_xpath      = "//p[contains(., 'Qt :')]"

    # Sélecteurs CSS pour extraction robuste
    product_ref            = "span[class*='reference']"  # Fallback CSS selector
    product_qty            = "p"  # Fallback CSS selector

    # suivi transporteur
    tracking_blocks        = "div.blocks .block"
    tracking_colis         = "div.colis div.block div.title"

 #   $ git config --global user.name "John Doe"
# $ git config --global user.email johndoe@example.com