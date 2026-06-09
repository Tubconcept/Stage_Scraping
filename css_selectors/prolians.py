"""
Sélecteurs CSS/XPath et URLs pour le site Prolians (www.prolians.fr — fournisseur P3).

Organisation :
  - Classe Selectors : tous les sélecteurs regroupés par zone (auth, produit, commandes, suivi).
  - Le site utilise beaucoup d'attributs data-testid (React) — privilégier ces sélecteurs.
  - Les XPath sont réservés aux structures dl/dt/dd difficiles à cibler en CSS.

Utilisation :
    from css_selectors.prolians import Selectors
    page.locator(Selectors.email_input).fill(user)
"""

class Selectors:
    """Constantes de sélection DOM pour Prolians — ne pas instancier."""

    # ─── URLs de base ────────────────────────────────────────────────────────
    BASE_URL: str = "https://www.prolians.fr"
    SITEMAP_INDEX = f"{BASE_URL}/sitemap.xml"   # Index des sitemaps produits
    LOGIN_URL: str = f"{BASE_URL}/login"

    # ─── Formulaire de connexion (flux en 2 étapes : email puis mot de passe) ─
    email_input         = "input[type='email']"
    email_button        = "button[aria-label='Connexion / Inscription']"
    password_input      = "input[aria-label='Mot de passe']"
    submit_button       = "button#next"
    logged_in_check     = "button[aria-label='Mon compte']"      # Présent si connecté
    logged_out_check    = "a[aria-label='Connectez-vous']"       # Présent si déconnecté

    # ─── Navigation générale ─────────────────────────────────────────────────
    cookie_acceptt       = "button[aria-label*='Accepter']"       # Variante courte bannière
    breadcrumb           = '[data-testid="breadcrumb/desktop"] a'

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE DÉTAIL PRODUIT — champs alignés sur CSV_HEADERS (core.config)
    # ══════════════════════════════════════════════════════════════════════════
    # inline_list_item : bloc Code P, EAN, réf. fabricant, réf. PROLIANS
    inline_list_item    = "div[data-testid='inline-list-item']"
    title               = 'h1[data-testid="title"]'
    conditionnement     = "span.text-sm.font-medium.text-brand-2-800"
    attributes_row      = '[data-testid="simple-table-row"]'
    brand_name          = 'a[href^="/brand/"] h2[data-testid="title"]'
    brand_image         = 'img[src*="descours-cabaud"]'
    description_button  = 'button[data-testid="disclosure-button"]'  # Ouvre le panneau description
    description_content = '.legacy-cms-styles'
    documents           = '[data-testid="document-tag-link"]'
    image_swiper        = '.swiper-wrapper .swiper-slide img'
    image_fallback      = '[data-testid="product-cloudflare-picture/image"]'
    price               = '[data-testid="product-price/price"] span'
    price_message       = '[data-testid="product-price-message"]'   # « Sur devis », etc.
    eco_tax             = '[data-testid="product-price/eco-participation"]'
    reduction           = '[data-testid="product-price/discount"]'
    combinations        = '[role="radiogroup"] input'              # Boutons radio déclinaisons
    cross_sell_ref      = 'span[data-testid="product-card/reference"]'
    eco_labels          = 'img[alt*="eco" i], img[alt*="certif" i], [class*="eco-label"]'

    # ══════════════════════════════════════════════════════════════════════════
    # COMMANDES — liste et détail
    # ══════════════════════════════════════════════════════════════════════════
    # Bannières cookies (plusieurs fournisseurs de consentement possibles)
    cookie_accept       = "button[aria-label='Accepter & Fermer: Accepter notre traitement des données et fermer']"
    didomi_accept       = "#didomi-notice-agree-button"
    accept_all_xpath    = "//button[contains(., 'Tout accepter')]"

    # Navigation vers la liste des commandes
    view_all_orders     = "a[aria-label='Voir toutes mes commandes']"
    order_row           = "tr[data-key^='WP-']"          # Ligne tableau ; data-key = ref web
    order_webref        = "td[id$='-webRef']"
    order_internalref   = "td[id$='-internalRef']"
    order_date          = "td[id$='-orderDate']"
    order_status        = "td[id$='-status']"
    next_page           = "button[aria-label='Page suivante']"
    tracking_button     = "a[aria-label='Suivre ma commande']"

    # Détail commande — XPath pour les paires label/valeur
    client_order_ref_xpath = "//dt[contains(., 'N° commande client')]/following-sibling::dd[1]"
    product_name           = 'span[data-testid="order-item/product-name"]'
    product_ref_xpath      = "//span[contains(., 'Réf. PROLIANS :')]"
    prodcut_qty_xpath      = "//p[contains(., 'Qt :')]"   # Typo historique conservée

    # Fallbacks CSS si les XPath échouent
    product_ref            = "span[class*='reference']"
    product_qty            = "p"

    # ══════════════════════════════════════════════════════════════════════════
    # RECHERCHE PAR RÉFÉRENCE
    # ══════════════════════════════════════════════════════════════════════════
    search_url_template = "https://www.prolians.fr/search?q="   # + encoded ref
    search_result_link  = 'a[data-testid="product-card/product-link"]'
    search_result_ref   = 'span[data-testid="product-card/reference"]'

    # ══════════════════════════════════════════════════════════════════════════
    # SUIVI LIVRAISON — page transporteur / colis
    # ══════════════════════════════════════════════════════════════════════════
    tracking_blocks        = "div.blocks .block"
    tracking_colis         = "div.colis div.block div.title"
