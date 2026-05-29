"""
Chef d'orchestre du scraper Prolians (mode : tracking) — point d'entrée officiel.

Ce fichier dirige l'ordre d'exécution en appelant les fonctions CSS de
scraper_prolians_tracking.py. Il contient main(), init_csv(), append_to_csv()
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
from scrapers.Prolians_P3.tracking.scraper_prolians_tracking import (
    navigate_to_orders, collect_orders_with_tracking, get_order_detail, log_exception
)

ROOT = PROJECT_ROOT
load_dotenv(ROOT / ".env")

User     = os.getenv("User_P3")
Password = os.getenv("Password_P3")

today    = datetime.today().strftime('%Y-%m-%d')
run_ts   = datetime.today().strftime('%Y-%m-%d_%H-%M')
csv_path = str(ROOT / f"csv/scrap_p3_Tracking_{run_ts}.csv")

DAYS_BACK  = 7
CSV_HEADER = [
    "id_cmd", "ref_cmd", "date_cmd", "statut_cmd", "data_pdt",
    "Date_Reliquat", "weight_exp", "carrier_exp", "trackinglink_exp", "tracking_exp",
]


# =============================
# CSV
# =============================

def init_csv(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode='w', newline='', encoding='utf-8') as f:
        csv.writer(f, delimiter=';').writerow(CSV_HEADER)

def append_to_csv(path, row):
    with open(path, mode='a', newline='', encoding='utf-8') as f:
        csv.writer(f, delimiter=';').writerow(row)


# =============================
# MAIN
# =============================

def main():
    date_sup = datetime.today()
    date_inf = datetime.today() - timedelta(days=DAYS_BACK)

    print(f"Tracking : {date_inf.strftime('%d/%m/%Y')} → {date_sup.strftime('%d/%m/%Y')}")
    print(f"CSV      : {csv_path}")

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
        orders = collect_orders_with_tracking(page, date_inf, date_sup)
        print(f"\n{len(orders)} commande(s) trouvée(s)\n")

        init_csv(csv_path)

        for order in orders:
            webref = order["webref"]
            try:
                detail = get_order_detail(page, order)
                if detail is None:
                    row = [webref, "", order.get("date", ""), order.get("status", ""),
                           "", "", "", "", "", ""]
                else:
                    row = [
                        detail["id_cmd"],
                        detail["ref_cmd"],
                        detail["date_cmd"],
                        detail["statut_cmd"],
                        detail["data_pdt"],
                        detail["date_reliquat"],
                        detail["weight"],
                        detail["carrier"],
                        detail["tracking_link"],
                        detail["tracking_number"],
                    ]
                append_to_csv(csv_path, row)
                print(f"  {webref} → CSV")
            except Exception as e:
                log_exception(e, f"Commande {webref}")
                row = [webref, "", order.get("date", ""), order.get("status", ""),
                       "", "", "", "", "", ""]
                append_to_csv(csv_path, row)
                print(f"  {webref} → Erreur, ligne vide écrite")

        browser.close()
        print(f"\nCSV généré : {csv_path}")


if __name__ == '__main__':
    main()


# =============================
# INTERFACE GUI
# =============================

import asyncio


class ProlianTrackingScraper:
    """Wrapper async du scraper Prolians tracking pour la GUI."""

    def __init__(self):
        load_dotenv(ROOT / ".env")
        self._user = os.getenv("User_P3")
        self._password = os.getenv("Password_P3")
        self._stop_requested = False

    async def run(self):
        await asyncio.to_thread(self._sync_run)

    def _sync_run(self):
        date_sup = datetime.today()
        date_inf = datetime.today() - timedelta(days=DAYS_BACK)
        run_ts = datetime.today().strftime('%Y-%m-%d_%H-%M')
        path = str(ROOT / f"csv/scrap_p3_Tracking_{run_ts}.csv")

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
            orders = collect_orders_with_tracking(page, date_inf, date_sup)
            print(f"{len(orders)} commande(s) trouvée(s)")

            init_csv(path)

            for order in orders:
                if self._stop_requested:
                    break
                webref = order["webref"]
                try:
                    detail = get_order_detail(page, order)
                    if detail is None:
                        row = [webref, "", order.get("date", ""), order.get("status", ""),
                               "", "", "", "", "", ""]
                    else:
                        row = [
                            detail["id_cmd"], detail["ref_cmd"], detail["date_cmd"],
                            detail["statut_cmd"], detail["data_pdt"], detail["date_reliquat"],
                            detail["weight"], detail["carrier"], detail["tracking_link"],
                            detail["tracking_number"],
                        ]
                    append_to_csv(path, row)
                    print(f"  {webref} → CSV")
                except Exception as e:
                    log_exception(e, f"Commande {webref}")
                    append_to_csv(path, [webref, "", order.get("date", ""),
                                         order.get("status", ""), "", "", "", "", "", ""])

            browser.close()
            print(f"CSV généré : {path}")

    def request_stop(self):
        self._stop_requested = True


def create_scraper() -> ProlianTrackingScraper:
    return ProlianTrackingScraper()
