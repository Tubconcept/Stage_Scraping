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
    # ⚠️ Relevé en live le 06/08/2026 : le formulaire a changé. Ciblage par NOM,
    # TYPE et TEXTE — surtout pas par ``id`` (générés par react-aria : « «r53» »)
    # ni par ``aria-label`` du mot de passe, qui porte désormais le libellé entier
    # « Saisissez votre mot de passe provisoire » et non plus « Mot de passe ».
    email_input         = "input[name='email']"
    email_button        = "button:has-text('Connexion / Inscription')"
    # ``data-testid`` en tête, ``type`` en repli (le name suit le libellé affiché).
    password_input      = "input[data-testid='password'], input[type='password']"
    # ⚠️ Bouton de la 2ᵉ étape : « Se connecter », DIFFÉRENT de celui de l'étape 1.
    # Ce n'est plus ``button#next`` — c'est ce qui bloquait la reconnexion.
    submit_button       = "button:has-text('Se connecter')"
    logged_in_check     = "button[aria-label='Mon compte']"      # Présent si connecté
    logged_out_check    = "a[href='/customer/account/login']"       # Présent si déconnecté

    # ─── Navigation générale ─────────────────────────────────────────────────
    cookie_acceptt       = "button[aria-label*='Accepter']"       # Variante courte bannière
    breadcrumb           = '[data-testid="breadcrumb/desktop"] a'

    # ══════════════════════════════════════════════════════════════════════════
    # NAVIGATION PAR CATÉGORIE (marche des URLs /nos-produits)
    # ══════════════════════════════════════════════════════════════════════════
    # Le méga-menu « Produits » est un composant React sans <a href> et avec un
    # overlay qui intercepte les clics → on n'essaie PAS de le piloter.
    # À la place, on navigue directement les URLs de catégorie, toutes propres
    # et hiérarchiques : /nos-produits/{slug}/{slug}/...  (cf. breadcrumb).
    PRODUCTS_ROOT_URL = f"{BASE_URL}/nos-produits"

    # Liens de (sous-)catégorie : tout <a> pointant sous /nos-produits/
    category_link     = 'a[href^="/nos-produits/"]'
    # Conteneur listing + cartes produit (le testid "product-card/product-link"
    # de la recherche n'existe PAS ici ; on prend l'<a> à l'intérieur de la carte)
    product_list      = '[data-testid="product-list"]'
    product_card      = '[data-testid="product-card"]'
    product_card_link = '[data-testid="product-card"] a[href]'
    # Pagination par URL : ?p=2, ?p=3 … (param « p », pas « page »)
    pagination_link   = 'a[href*="?p="], a[href*="&p="]'
    # Bouton « Page suivante » : composant React sans <a href> (data-react-aria),
    # désactivé sur la dernière page. C'est lui qui pilote la pagination du
    # listing — les liens numérotés ?p=N ne sont pas toujours présents.
    pagination_next   = 'button[aria-label="Page suivante"]'

    # Catégories principales : nom affiché (GUI) → slug d'URL sous /nos-produits
    CATEGORY_URLS = {
        "EPI - Protection individuelle":                              "epi-protection-individuelle",
        "Outillage":                                                  "outillage",
        "Consommables":                                               "consommables",
        "Quincaillerie":                                              "quincaillerie",
        "Boulonnerie-visserie et Fixations":                          "boulonnerie-visserie-et-fixations",
        "Soudage, machines et équipements d'atelier":                 "soudage-machines-et-equipements-d-atelier",
        "Plomberie, Sanitaire, Chauffage, Climatisation et Pompage":  "plomberie-sanitaire-chauffage-climatisation-et-pompage",
        "Équipements de chantier":                                    "equipements-de-chantier",
        "Électricité":                                                "electricite",
        "Aménagements extérieurs":                                    "amenagements-exterieurs",
    }
    # Entrée GUI pour scraper tout le catalogue (parcourt les 10 catégories).
    # Placée EN FIN de liste pour que le défaut GUI (1er élément) soit une vraie
    # catégorie — évite un crawl complet déclenché par mégarde.
    ALL_CATEGORIES_LABEL = "Toutes les catégories"
    CATEGORY_NAMES = list(CATEGORY_URLS.keys()) + [ALL_CATEGORIES_LABEL]

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
    # Page adresses client — sélecteurs pour la suppression d'adresses (suppradrr)
    addresses_url       = f"{BASE_URL}/customer/addresses"
    address_card        = "div[data-testid='card']"
    delete_address_btn  = "button[aria-label='Delete address']"
    confirm_button      = "button[aria-label='Valider']"

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
    product_qty_badge      = "p.bg-brand-2-50"   # badge bleu "Qt : N" sur la fiche commande

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
