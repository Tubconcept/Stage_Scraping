"""
Chef d'orchestre du scraper Prolians (mode : commandes).

Point d'entrée CLI : demande une plage de dates, ouvre Playwright,
navigue vers l'historique des commandes, délègue la collecte et le parsing
à ``scraper_prolians_orders``, puis persiste chaque commande dans
``prolians.db`` (table commandes SQLite).

Ne contient pas de sélecteurs CSS : toute l'extraction DOM est dans le
module ``scraper_prolians_orders``.
"""
import sys
from pathlib import Path

# --- Racine projet pour imports absolus (auth, db, selectors) ---
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from auth.prolians.cookie_manager import ensure_logged_in
from scrapers.Prolians_P3.orders.scraper_prolians_orders import (
    navigate_to_orders, collect_orders, get_info, log, log_exception
)
from db.mariadb_db import init_site_db, insert_order as _db_insert_order

ROOT = PROJECT_ROOT
load_dotenv(ROOT / ".env")

# Compte B2B Prolians (variables d'environnement)
User     = os.getenv("User_P3")
Password = os.getenv("Password_P3")


# =============================
# Persistance SQLite
# =============================

def persist_order(data_dict):
    """
    Mappe le dictionnaire métier issu de ``get_info`` vers le schéma SQLite
    et insère une ligne (erreurs silencieuses pour ne pas bloquer le lot).
    """
    row = {
        "id_cmd":     data_dict.get("webref", ""),      # référence web Prolians
        "ref_cmd":    data_dict.get("ref_cmd", ""),     # référence client (PO)
        "date_cmd":   data_dict.get("date_cmd", ""),
        "statut_cmd": data_dict.get("statut_cmd", ""),
        "data_pdt":   data_dict.get("prdt_data", ""),   # lignes produits concaténées
    }
    try:
        _db_insert_order(_prolians_orders_db(), "prolians", row)
    except Exception as exc:
        log.error(f"persist_order : {exc}")


# Connexion SQLite réutilisée sur toute la session CLI
_prolians_orders_conn = None


def _prolians_orders_db():
    """Initialise paresseusement la base ``prolians.db`` au premier appel."""
    global _prolians_orders_conn
    if _prolians_orders_conn is None:
        _prolians_orders_conn = init_site_db("prolians")
    return _prolians_orders_conn


# =============================
# MAIN
# =============================

def main():
    """Boucle principale : saisie des dates → collecte → détail → MariaDB."""
    inputsup = input("Fournir la date supérieure de l'intervalle (format d/m/yyyy) : ")
    inputinf = input("Fournir la date inférieure de l'intervalle (format d/m/yyyy) : ")

    # Borne haute : défaut = aujourd'hui si format invalide
    try:
        date_sup = datetime.strptime(inputsup, "%d/%m/%Y")
    except Exception:
        date_sup = datetime.today()

    # Borne basse : défaut = 31 derniers jours si format invalide
    try:
        date_inf = datetime.strptime(inputinf, "%d/%m/%Y")
    except Exception:
        date_inf = datetime.today() - timedelta(days=31)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.set_default_timeout(10000)
        context.set_default_navigation_timeout(15000)
        page = context.new_page()

        if not ensure_logged_in(page, context, User, Password):
            log.error("Connexion Prolians échouée — arrêt.")
            browser.close()
            return

        navigate_to_orders(page)

        log.info(f"Collecte des commandes du {date_inf.strftime('%d/%m/%Y')} au {date_sup.strftime('%d/%m/%Y')}")
        # Liste légère (webref, date, statut) depuis le tableau paginé
        orders = collect_orders(page, date_inf, date_sup)
        log.info(f"{len(orders)} commande(s) trouvée(s)")

        # Une navigation par commande vers la page détail pour les lignes produits
        for order in orders:
            try:
                data = get_info(page, order)
                if data:
                    persist_order(data)
                    log.debug(f"{order['webref']} -> MariaDB")
            except Exception as e:
                log_exception(log, e, f"Commande {order['webref']}")

        browser.close()
        log.info("Commandes enregistrées en MariaDB")


if __name__ == "__main__":
    main()
