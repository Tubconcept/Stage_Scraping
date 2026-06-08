"""
Orchestrateur du scraper commandes Legallais (site P1).

Rôle :
    Demande une plage de dates à l'utilisateur, se connecte via Playwright,
    parcourt la liste DataTables des commandes et enregistre chaque commande
    dans legallais.db.

Type : commandes.

Architecture :
    - scrap_legallais_orders.py (ce fichier) = orchestrateur : session
      (cookie_manager_legallais), boucles de pagination, persist_order().
    - scraper_legallais_orders.py = moteur CSS : sélecteurs, get_url_cmd(),
      get_Info(), helpers dates et nettoyage texte.

Aucune logique CSS ne doit rester dans ce fichier.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
from db.sqlite_db import init_site_db, insert_order as _db_insert_order

load_dotenv(_PROJECT_ROOT / ".env")

# Fonctionne à la fois en import package (GUI) et en script standalone (CLI)
try:
    from .scraper_legallais_orders import (
        today, BASE_URL, NEXT_PAGE_BUTTON,
        log_exception, get_url_cmd, check_date, get_Info,
    )
except ImportError:
    from scrapers.Legallais_P1.orders.scraper_legallais_orders import (  # type: ignore[no-redef]
        today, BASE_URL, NEXT_PAGE_BUTTON,
        log_exception, get_url_cmd, check_date, get_Info,
    )

from auth.legallais.cookie_manager_legallais import ensure_logged_in, get_session_state

# ─── Constantes ───────────────────────────────────────────────────────────────
LEGALLAIS_EMAIL   = os.getenv("User_P1", "")
LEGALLAIS_PASSWORD = os.getenv("Password_P1", "")


# ─── Persistance SQLite ───────────────────────────────────────────────────────

def persist_order(data_dict):
    row = {
        "id_cmd":     data_dict.get("ref_p1", ""),
        "ref_cmd":    data_dict.get("ref_cmd", ""),
        "date_cmd":   data_dict.get("date_cmd", ""),
        "statut_cmd": data_dict.get("statut", ""),
        "data_pdt":   data_dict.get("prdt_data", ""),
    }
    try:
        _db_insert_order(_legallais_db(), "legallais", row)
    except Exception:
        pass


# Module-level DB connection — opened lazily, one per process lifetime.
_legallais_conn = None


def _legallais_db():
    global _legallais_conn
    if _legallais_conn is None:
        _legallais_conn = init_site_db("legallais")
    return _legallais_conn


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    # Saisie interactive de la plage [DateInf, DateSup] au format JJ/MM/AAAA
    inputSup = input("Fournisser la date supérieur de l'intervalle de temps veuillez écrire une date au format d/m/yyyy ")
    inputInf = input("Fournisser la date inférieur de l'intervalle de temps veuillez écrire une date au format d/m/yyyy ")

    try:
        DateInf = datetime.strptime(inputInf, "%d/%m/%Y")
    except Exception:
        DateInf = datetime.today() - timedelta(days=3)

    try:
        DateSup = datetime.strptime(inputSup, "%d/%m/%Y")
    except Exception:
        DateSup = datetime.today()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        # Charger la session du jour si disponible (même pattern que Prolians/Setin)
        storage = get_session_state()
        context = browser.new_context(storage_state=storage)
        page    = context.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})

        if not ensure_logged_in(page, context, LEGALLAIS_EMAIL, LEGALLAIS_PASSWORD):
            print("Impossible de se connecter à Legallais.")
            browser.close()
            return

        page.goto(BASE_URL + "/user/order")
        page.wait_for_load_state("domcontentloaded")

        Url_cmd = []
        # Phase 1 : avancer dans les pages jusqu'à atteindre DateSup (borne haute)
        check_time_sup = check_date(page, DateSup)
        while not check_time_sup:
            try:
                old_first_row = page.locator("tbody tr").first
                old_text = old_first_row.inner_text()
                page.locator(NEXT_PAGE_BUTTON).click()
                page.wait_for_function(
                    """(oldText) => {
                        const firstRow = document.querySelector('tbody tr');
                        return firstRow && firstRow.innerText !== oldText;
                    }""",
                    arg=old_text
                )
                check_time_sup = check_date(page, DateSup)
            except Exception as e:
                log_exception(today, e, "érreur de bouclage pour le tab commandes")
                break

        for cmd in get_url_cmd(page):
            try:
                row_date = datetime.strptime(cmd["date_str"], "%d/%m/%Y")
            except Exception:
                Url_cmd.append(cmd)
                continue
            if DateInf <= row_date <= DateSup:
                Url_cmd.append(cmd)

        # Phase 2 : continuer à paginer et collecter tant qu'on n'a pas dépassé DateInf
        check_time_inf = check_date(page, DateInf)
        while not check_time_inf:
            try:
                old_first_row = page.locator("tbody tr").first
                old_text = old_first_row.inner_text()
                page.locator(NEXT_PAGE_BUTTON).click()
                page.wait_for_function(
                    """(oldText) => {
                        const firstRow = document.querySelector('tbody tr');
                        return firstRow && firstRow.innerText !== oldText;
                    }""",
                    arg=old_text
                )
                for cmd in get_url_cmd(page):
                    try:
                        row_date = datetime.strptime(cmd["date_str"], "%d/%m/%Y")
                    except Exception:
                        Url_cmd.append(cmd)
                        continue
                    if DateInf <= row_date <= DateSup:
                        Url_cmd.append(cmd)
                check_time_inf = check_date(page, DateInf)
            except Exception as e:
                log_exception(today, e, "érreur de bouclage pour le tab commandes")
                break

        print(f"{len(Url_cmd)} de commande")

        for cmd in Url_cmd:
            try:
                page.goto(BASE_URL + cmd['link'])
                page.wait_for_load_state("domcontentloaded")
                Commande = get_Info(page, cmd)
                print(Commande)
                persist_order(Commande)
            except Exception as e:
                log_exception(today, e, f"érreur page {cmd['link']}")

        if Url_cmd:
            print(Url_cmd[-1])

        browser.close()


if __name__ == "__main__":
    main()
