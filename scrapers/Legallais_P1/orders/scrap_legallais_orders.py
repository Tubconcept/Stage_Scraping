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
      get_info(), helpers dates et nettoyage texte.

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
from db.mariadb_db import init_site_db, insert_order as _db_insert_order

load_dotenv(_PROJECT_ROOT / ".env")

# Fonctionne à la fois en import package (GUI) et en script standalone (CLI)
try:
    from .scraper_legallais_orders import (
        BASE_URL, NEXT_PAGE_BUTTON,
        log, log_exception, get_url_cmd, check_date, get_info,
    )
except ImportError:
    from scrapers.Legallais_P1.orders.scraper_legallais_orders import (  # type: ignore[no-redef]
        BASE_URL, NEXT_PAGE_BUTTON,
        log, log_exception, get_url_cmd, check_date, get_info,
    )

from auth.legallais.cookie_manager_legallais import ensure_logged_in, get_session_state

# ─── Constantes ───────────────────────────────────────────────────────────────
LEGALLAIS_EMAIL   = os.getenv("User_P1", "")
LEGALLAIS_PASSWORD = os.getenv("Password_P1", "")
DATE_FORMAT = "%d/%m/%Y"


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


# ─── Helpers d'orchestration ──────────────────────────────────────────────────

def _parse_date_range():
    """Prompt user for date range and return (dateinf, datesup) with fallback defaults."""
    inputsup = input("Fournisser la date supérieur de l'intervalle de temps veuillez écrire une date au format d/m/yyyy ")
    inputinf = input("Fournisser la date inférieur de l'intervalle de temps veuillez écrire une date au format d/m/yyyy ")

    try:
        dateinf = datetime.strptime(inputinf, DATE_FORMAT)
    except Exception:
        dateinf = datetime.today() - timedelta(days=3)

    try:
        datesup = datetime.strptime(inputsup, DATE_FORMAT)
    except Exception:
        datesup = datetime.today()

    return dateinf, datesup


def _next_page(page):
    """Click the next-page button and wait for the table rows to refresh."""
    old_text = page.locator("tbody tr").first.inner_text()
    page.locator(NEXT_PAGE_BUTTON).click()
    page.wait_for_function(
        """(oldText) => {
            const firstRow = document.querySelector('tbody tr');
            return firstRow && firstRow.innerText !== oldText;
        }""",
        arg=old_text,
    )


def _advance_to_page_with_date(page, target_date):
    """Paginate forward until target_date is visible in the current page."""
    while not check_date(page, target_date):
        try:
            _next_page(page)
        except Exception as e:
            log_exception(log, e, "érreur de bouclage pour le tab commandes")
            break


def _collect_page_in_range(page, dateinf, datesup):
    """Return commands from the current page whose date falls in [dateinf, datesup]."""
    collected = []
    for cmd in get_url_cmd(page):
        try:
            row_date = datetime.strptime(cmd["date_str"], DATE_FORMAT)
        except Exception:
            collected.append(cmd)
            continue
        if dateinf <= row_date <= datesup:
            collected.append(cmd)
    return collected


def _paginate_and_collect(page, dateinf, datesup, url_cmd):
    """Phase 2 : paginate until dateinf is reached, collecting matching commands."""
    while not check_date(page, dateinf):
        try:
            _next_page(page)
            url_cmd.extend(_collect_page_in_range(page, dateinf, datesup))
        except Exception as e:
            log_exception(log, e, "érreur de bouclage pour le tab commandes")
            break


def _process_all_cmds(page, url_cmd):
    """Visit each command URL, extract its data, and persist it to the DB."""
    for cmd in url_cmd:
        try:
            page.goto(BASE_URL + cmd["link"])
            page.wait_for_load_state("domcontentloaded")
            commande = get_info(page, cmd)
            log.debug(f"Commande extraite : {commande}")
            persist_order(commande)
        except Exception as e:
            log_exception(log, e, f"érreur page {cmd['link']}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    dateinf, datesup = _parse_date_range()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        storage = get_session_state()
        context = browser.new_context(storage_state=storage)
        page = context.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})

        if not ensure_logged_in(page, context, LEGALLAIS_EMAIL, LEGALLAIS_PASSWORD):
            log.error("Impossible de se connecter à Legallais.")
            browser.close()
            return

        page.goto(BASE_URL + "/user/order")
        page.wait_for_load_state("domcontentloaded")

        _advance_to_page_with_date(page, datesup)
        url_cmd = _collect_page_in_range(page, dateinf, datesup)
        _paginate_and_collect(page, dateinf, datesup, url_cmd)

        log.info(f"{len(url_cmd)} commande(s) à traiter")
        _process_all_cmds(page, url_cmd)

        if url_cmd:
            log.debug(f"Dernière commande : {url_cmd[-1]}")

        browser.close()


if __name__ == "__main__":
    main()
