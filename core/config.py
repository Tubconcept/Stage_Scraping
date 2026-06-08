"""
Configuration globale du projet — constantes partagées par tous les scrapers.

Ce fichier centralise :
  - Les en-têtes CSV (source unique de vérité pour les schémas SQLite)
  - Les timeouts Playwright
  - Les chemins de répertoires (logs, CSV, profils navigateur, bases SQLite)
  - Les paramètres d'affichage navigateur (viewport)

Les identifiants de connexion (User_P1, Password_P3, etc.) sont dans le fichier .env
à la racine du projet, jamais dans ce module.
"""

from pathlib import Path
import sys

# ═══════════════════════════════════════════════════════════════════════════════
# COLONNES CSV PRODUITS — alignées sur le format d'export attendu par Tubconcept
# Ces listes servent aussi à générer automatiquement les tables SQLite
# (voir db/sqlite_db.py → init_site_db).
# ═══════════════════════════════════════════════════════════════════════════════
CSV_HEADERS = [
    "product_fournisseur",           # Code fournisseur (P1, P3, P5…)
    "product_reference_fournisseur", # Référence interne du site
    "product_ean",                   # Code-barres EAN
    "product_reference_fabricant",   # Référence du fabricant
    "product_brand",                 # Nom de la marque
    "product_brand_logo_url",        # URL du logo marque
    "product_designation",           # Titre / désignation produit
    "product_description",           # Description longue ou courte
    "product_image_url",             # URL image principale
    "product_docs_url",              # Liens vers fiches techniques PDF
    "product_category_tree",         # Fil d'Ariane / arborescence catégorie
    "product_conditionnement",       # Unité de vente (pièce, lot, mètre…)
    "product_stock_status",          # Disponibilité (EN STOCK / PAS EN STOCK)
    "product_status",                # Statut commercial (fin de vie, etc.)
    "product_fournisseur_url",       # URL canonique de la fiche produit
    "product_eco_label",             # Labels écologiques
    "product_eco_taxe",              # Montant éco-participation
    "product_promotion",             # Prix barré / promo
    "product_price_ht",              # Prix HT
    "product_attributes",            # Caractéristiques techniques (clé:valeur)
    "products_is_combination",       # Produit à déclinaisons (oui/non)
    "product_combination_index",     # Index de la variante dans le tableau
    "product_parent_reference",      # Référence parente (déclinaisons)
    "product_child_reference",       # Référence enfant
    "product_combination_values",    # Valeurs des axes de déclinaison
    "product_cross_sell",            # Produits associés / cross-sell
]

# Colonnes pour l'export des commandes
ORDERS_CSV_HEADERS = [
    "id_cmd",      # Identifiant unique commande (clé de déduplication)
    "ref_cmd",     # Référence client / interne
    "date_cmd",    # Date de commande (JJ/MM/AAAA)
    "statut_cmd",  # Statut (Livré, En cours…)
    "data_pdt",    # Lignes produits encodées (ref:libellé:qté)
]

# Colonnes suivi — reprend les 5 champs commande + infos transporteur
TRACKING_CSV_HEADERS = [
    "id_cmd",
    "ref_cmd",
    "date_cmd",
    "statut_cmd",
    "data_pdt",
    "Date_Reliquat",    # Date de reliquat éventuelle
    "weight_exp",       # Poids colis
    "carrier_exp",      # Nom du transporteur
    "trackinglink_exp", # URL de suivi
    "tracking_exp",     # Numéro de suivi
]

# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGES D'ERREUR PLAYWRIGHT À IGNORER DANS LES LOGS
# (fermeture normale du navigateur en fin de scraper)
# ═══════════════════════════════════════════════════════════════════════════════
IGNORED_ERRORS = [
    "Target page, context or browser has been closed",
    "Browser has been closed",
    "TargetClosedError",
]

# ═══════════════════════════════════════════════════════════════════════════════
# TIMEOUTS PLAYWRIGHT (millisecondes)
# Utilisés par les scrapers async Setin et les helpers utils.safe_get_*
# ═══════════════════════════════════════════════════════════════════════════════
TIMEOUT_PAGE_LOAD = 30000   # Chargement complet d'une page
TIMEOUT_ELEMENT = 5000      # Attente d'un élément DOM visible
TIMEOUT_SHORT = 1000
TIMEOUT_MEDIUM = 3000
TIMEOUT_LONG = 7000

# Équivalents en secondes (pour Botasaurus ou time.sleep)
TIMEOUT_LONG_SEC = TIMEOUT_LONG // 1000
TIMEOUT_MEDIUM_SEC = TIMEOUT_MEDIUM // 1000
TIMEOUT_SHORT_SEC = TIMEOUT_SHORT // 1000
TIMEOUT_ELEMENT_SEC = TIMEOUT_ELEMENT // 1000
TIMEOUT_PAGE_LOAD_SEC = TIMEOUT_PAGE_LOAD // 1000


# ═══════════════════════════════════════════════════════════════════════════════
# CHEMINS DU PROJET
# DIRECTORY = racine du dépôt (parent du dossier core/)
# ═══════════════════════════════════════════════════════════════════════════════
DIRECTORY = Path(__file__).resolve().parent.parent
LOG_DIR = DIRECTORY / "log"                    # Fichiers journaliers scraper-YYYY-MM-DD.log
CSV_DIR = DIRECTORY / "csv"                    # Exports CSV ponctuels (Legallais produits…)
JSON_DIR = DIRECTORY / "json"                  # Données JSON intermédiaires si besoin
PROFILES_DIR = DIRECTORY / "playwright_profiles"  # Sessions navigateur par site

# Résolution d'écran simulée pour Playwright (évite les layouts mobile)
VIEWPORT = {"width": 1920, "height": 1080}
