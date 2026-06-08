"""
Chef d'orchestre du scraper Prolians (mode : suivi livraison).

Sur les ``DAYS_BACK`` derniers jours calendaires, collecte les commandes
ayant un lien « Suivre ma commande », extrait transporteur / poids / numéro
de tracking via ``scraper_prolians_tracking``, puis persiste les lignes dans
``prolians.db`` selon ``TRACKING_CSV_HEADERS``.

Expose aussi ``ProlianTrackingScraper`` pour la GUI (arrêt asynchrone).
"""
import sys
from pathlib import Path

# --- Racine projet ---
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from auth.prolians.cookie_manager import ensure_logged_in
from core.config import TRACKING_CSV_HEADERS
from scrapers.Prolians_P3.tracking.scraper_prolians_tracking import (
    navigate_to_orders, collect_orders_with_tracking, get_order_detail, log_exception
)
from db.mariadb_db import init_site_db, insert_tracking as _db_insert_tracking

ROOT = PROJECT_ROOT
load_dotenv(ROOT / ".env")

User     = os.getenv("User_P3")
Password = os.getenv("Password_P3")

# Fenêtre glissante par défaut (modifiable avant exécution si besoin)
DAYS_BACK = 7


# =============================
# Fenêtre temporelle & mapping lignes
# =============================

def _tracking_window():
    """Fenêtre inclusive des N derniers jours (minuit → fin de journée)."""
    date_sup = datetime.today().replace(hour=23, minute=59, second=59, microsecond=0)
    date_inf = (datetime.today() - timedelta(days=DAYS_BACK)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return date_inf, date_sup


def _row_to_dict(row: list) -> dict:
    """Associe chaque valeur de ligne à son en-tête CSV / colonne SQLite."""
    return dict(zip(TRACKING_CSV_HEADERS, row))


def _persist_tracking(db_conn, row: list) -> None:
    if db_conn is None:
        return
    try:
        _db_insert_tracking(db_conn, "prolians", _row_to_dict(row))
    except Exception as exc:
        print(f"[DB ERROR] _persist_tracking: {exc}")


def _build_row(webref: str, order: dict, detail: dict | None) -> list:
    """
    Construit une ligne tracking ordonnée selon ``TRACKING_CSV_HEADERS``.

    Si ``detail`` est absent (erreur d'extraction), enregistre au minimum
    l'identifiant web et les métadonnées de la liste commandes.
    """
    if detail is None:
        return [
            webref, "", order.get("date", ""), order.get("status", ""),
            "", "", "", "", "", "",
        ]
    return [
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


# =============================
# MAIN
# =============================

def main():
    """Exécution CLI : connexion → collecte → détail tracking → MariaDB."""
    date_inf, date_sup = _tracking_window()

    print(f"Tracking : {date_inf.strftime('%d/%m/%Y')} → {date_sup.strftime('%d/%m/%Y')}")

    db_conn = None
    try:
        db_conn = init_site_db("prolians")
    except Exception as exc:
        print(f"Base MariaDB Prolians non initialisée : {exc}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.set_default_timeout(10000)
        context.set_default_navigation_timeout(15000)
        page = context.new_page()

        if not ensure_logged_in(page, context, User, Password):
            print("Connexion échouée — arrêt.")
            browser.close()
            if db_conn:
                db_conn.close()
            return

        navigate_to_orders(page)
        orders = collect_orders_with_tracking(page, date_inf, date_sup)
        print(f"\n{len(orders)} commande(s) trouvée(s)\n")

        for order in orders:
            webref = order["webref"]
            try:
                detail = get_order_detail(page, order)
                row = _build_row(webref, order, detail)
                _persist_tracking(db_conn, row)
                print(f"  {webref} → MariaDB")
            except Exception as e:
                log_exception(e, f"Commande {webref}")
                # Ne pas perdre la commande : ligne partielle avec webref + date liste
                row = _build_row(webref, order, None)
                _persist_tracking(db_conn, row)
                print(f"  {webref} → Erreur, ligne minimale enregistrée")

        browser.close()
        if db_conn:
            db_conn.close()
        print("\nTracking enregistré en MariaDB")


if __name__ == '__main__':
    main()


# =============================
# INTERFACE GUI
# =============================

import asyncio


class ProlianTrackingScraper:
    """
    Wrapper async du scraper Prolians tracking pour la GUI.

    Recharge le ``.env`` à l'instanciation pour prendre en compte les identifiants
    saisis depuis l'interface sans redémarrer l'application.
    """

    def __init__(self):
        load_dotenv(ROOT / ".env")
        self._user = os.getenv("User_P3")
        self._password = os.getenv("Password_P3")
        self._stop_requested = False

    async def run(self):
        await asyncio.to_thread(self._sync_run)

    def _sync_run(self):
        """Corps synchrone Playwright, exécuté dans un thread via ``asyncio.to_thread``."""
        date_inf, date_sup = _tracking_window()

        db_conn = None
        try:
            db_conn = init_site_db("prolians")
        except Exception as exc:
            print(f"Base MariaDB Prolians non initialisée : {exc}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            context.set_default_timeout(10000)
            context.set_default_navigation_timeout(15000)
            page = context.new_page()

            if not ensure_logged_in(page, context, self._user, self._password):
                print("Connexion échouée — arrêt.")
                browser.close()
                if db_conn:
                    db_conn.close()
                return

            navigate_to_orders(page)
            orders = collect_orders_with_tracking(page, date_inf, date_sup)
            print(f"{len(orders)} commande(s) trouvée(s)")

            for order in orders:
                if self._stop_requested:
                    break
                webref = order["webref"]
                try:
                    detail = get_order_detail(page, order)
                    row = _build_row(webref, order, detail)
                    _persist_tracking(db_conn, row)
                    print(f"  {webref} → MariaDB")
                except Exception as e:
                    log_exception(e, f"Commande {webref}")
                    row = _build_row(webref, order, None)
                    _persist_tracking(db_conn, row)

            browser.close()
            if db_conn:
                db_conn.close()
            print("Tracking enregistré en MariaDB")

    def request_stop(self):
        self._stop_requested = True


def create_scraper() -> ProlianTrackingScraper:
    """Fabrique attendue par la configuration GUI du site Prolians."""
    return ProlianTrackingScraper()
