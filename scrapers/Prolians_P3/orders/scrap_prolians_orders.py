"""
Chef d'orchestre du scraper Prolians (mode : orders) — point d'entrée officiel.

Ce fichier dirige l'ordre d'exécution en appelant les fonctions CSS de
scraper_prolians_orders.py. Il contient run(), init_csv(), append_to_csv()
et la boucle principale de persistance.
"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os
import csv
from datetime import datetime, timedelta
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from auth.prolians.cookie_manager_prolians import ensure_logged_in
from scrapers.Prolians_P3.orders.scraper_prolians_orders import (
    navigate_to_orders, collect_orders, get_info, log_exception
)

ROOT = PROJECT_ROOT
load_dotenv(ROOT / ".env")

User     = os.getenv("User_P3")
Password = os.getenv("Password_P3")
today    = datetime.today().strftime('%Y-%m-%d')
run_ts   = datetime.today().strftime('%Y-%m-%d_%H-%M')
csv_path = str(ROOT / f"csv/scrap_p3_CMD_{run_ts}.csv")


# =============================
# CSV
# =============================

def init_csv(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    header = ["id_cmd", "ref_cmd", "date_cmd", "statut_cmd", "data_pdt"]
    with open(path, mode='w', newline='', encoding='utf-8') as file:
        csv.writer(file, delimiter=';').writerow(header)

def append_to_csv(path, data_dict):
    with open(path, mode='a', newline='', encoding='utf-8') as file:
        csv.writer(file, delimiter=';').writerow([
            data_dict.get("ref_px", ""),
            data_dict.get("ref_cmd", ""),
            data_dict.get("date_cmd", ""),
            data_dict.get("statut_cmd", ""),
            data_dict.get("prdt_data", ""),
        ])


# =============================
# MAIN
# =============================

def main():
    inputSup = input("Fournir la date supérieure de l'intervalle (format d/m/yyyy) : ")
    inputInf = input("Fournir la date inférieure de l'intervalle (format d/m/yyyy) : ")

    try:
        date_sup = datetime.strptime(inputSup, "%d/%m/%Y")
    except:
        date_sup = datetime.today()

    try:
        date_inf = datetime.strptime(inputInf, "%d/%m/%Y")
    except:
        date_inf = datetime.today() - timedelta(days=31)

    print(f"\n CSV : {csv_path}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.set_default_timeout(10000)
        context.set_default_navigation_timeout(15000)
        page = context.new_page()

        if not ensure_logged_in(page, context, User, Password):
            print("Connexion échouée — arrêt.")
            browser.close()
            return

        navigate_to_orders(page)

        init_csv(csv_path)

        print(f"\nCollecte des commandes du {date_inf.strftime('%d/%m/%Y')} au {date_sup.strftime('%d/%m/%Y')}")
        orders = collect_orders(page, date_inf, date_sup)
        print(f"\n{len(orders)} commande(s) trouvée(s)")

        for order in orders:
            try:
                data = get_info(page, order)
                if data:
                    append_to_csv(csv_path, data)
                    print(f"  {order['webref']} -> CSV")
            except Exception as e:
                log_exception(today, e, f"Commande {order['webref']}")

        browser.close()
        print(f"\nCSV généré : {csv_path}")


if __name__ == "__main__":
    main()


# =============================
# INTERFACE GUI
# =============================

import asyncio


class ProlianOrderScraper:
    """Wrapper async du scraper Prolians orders pour la GUI."""

    def __init__(self, date_from: datetime, date_to: datetime):
        load_dotenv(ROOT / ".env")
        self._user = os.getenv("User_P3")
        self._password = os.getenv("Password_P3")
        self.date_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
        self.date_to = date_to.replace(hour=23, minute=59, second=59, microsecond=0)
        self._stop_requested = False

    async def run(self):
        await asyncio.to_thread(self._sync_run)

    def _sync_run(self):
        run_ts = datetime.today().strftime('%Y-%m-%d_%H-%M')
        path = str(ROOT / f"csv/scrap_p3_CMD_{run_ts}.csv")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            context.set_default_timeout(10000)
            context.set_default_navigation_timeout(15000)
            page = context.new_page()

            if not ensure_logged_in(page, context, self._user, self._password):
                print("Connexion échouée — arrêt.")
                browser.close()
                return

            navigate_to_orders(page)
            init_csv(path)

            today_str = datetime.today().strftime('%Y-%m-%d')
            orders = collect_orders(page, self.date_from, self.date_to)
            print(f"{len(orders)} commande(s) trouvée(s)")

            for order in orders:
                if self._stop_requested:
                    break
                try:
                    data = get_info(page, order)
                    if data:
                        append_to_csv(path, data)
                        print(f"  {order['webref']} -> CSV")
                except Exception as e:
                    log_exception(today_str, e, f"Commande {order['webref']}")

            browser.close()
            print(f"CSV généré : {path}")

    def request_stop(self):
        self._stop_requested = True


def create_scraper(date_from: datetime, date_to: datetime) -> ProlianOrderScraper:
    return ProlianOrderScraper(date_from=date_from, date_to=date_to)
