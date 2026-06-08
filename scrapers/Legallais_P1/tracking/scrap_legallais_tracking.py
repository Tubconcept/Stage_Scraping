"""
Orchestrateur du scraper suivi / tracking Legallais (site P1).

Rôle :
    Parcourt les commandes des 7 derniers jours (fenêtre par défaut), extrait
    transporteur, lien de suivi, poids, reliquat et persiste en SQLite.

Type : suivi (tracking).

Architecture :
    - scrap_legallais_tracking.py (ce fichier) = orchestrateur : décorateur
      @browser Botasaurus, persist_tracking(), boucle pagination.
    - scraper_legallais_tracking.py = moteur CSS : connexion, modal suivi colis,
      détection Chronopost/TNT, extraction reliquat.

Aucune logique CSS ne doit rester dans ce fichier.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os
from datetime import datetime

from botasaurus.browser import browser, Driver
from dotenv import load_dotenv
from db.sqlite_db import init_site_db, insert_tracking as _db_insert_tracking

# Fonctionne à la fois en import package (GUI) et en script standalone (CLI)
try:
    from .scraper_legallais_tracking import (
        today, BASE_URL, NEXT_PAGE_BUTTON,
        log_exception, connexion, check_date, get_url_cmd, get_Info,
    )
except ImportError:
    from scrapers.Legallais_P1.tracking.scraper_legallais_tracking import (  # type: ignore[no-redef]
        today, BASE_URL, NEXT_PAGE_BUTTON,
        log_exception, connexion, check_date, get_url_cmd, get_Info,
    )

load_dotenv()

LEGALLAIS_EMAIL   = os.getenv("User_P1")
LEGALLAIS_PASSWORD = os.getenv("Password_P1")

# Flags de configuration (conservés pour compatibilité ; non utilisés dans main actuel)
DRY_RUN    = True
HEADLESS   = False
HUMAN_MODE = True


# ─── Persistance SQLite ───────────────────────────────────────────────────────

def persist_tracking(data_dict):
    row = {
        "id_cmd":          data_dict.get("ref_p1", ""),
        "ref_cmd":         data_dict.get("ref_cmd", ""),
        "date_cmd":        data_dict.get("date_cmd", ""),
        "statut_cmd":      data_dict.get("statut", ""),
        "data_pdt":        data_dict.get("prdt_data", ""),
        "Date_Reliquat":   data_dict.get("dateReliquat", ""),
        "weight_exp":      data_dict.get("weight", ""),
        "carrier_exp":     data_dict.get("transproteur", ""),
        "trackinglink_exp": data_dict.get("suivi", ""),
        "tracking_exp":    data_dict.get("numero_suivi", ""),
    }
    try:
        _db_insert_tracking(_legallais_tracking_db(), "legallais", row)
    except Exception:
        pass


# Module-level DB connection — opened lazily, one per process lifetime.
_legallais_tracking_conn = None


def _legallais_tracking_db():
    global _legallais_tracking_conn
    if _legallais_tracking_conn is None:
        _legallais_tracking_conn = init_site_db("legallais")
    return _legallais_tracking_conn


# ─── MAIN ─────────────────────────────────────────────────────────────────────

@browser(block_images=True, headless=False)
def main(driver: Driver, _data=None):
    driver.enable_human_mode()
    print("Ouverture de session…")
    connexion(driver, LEGALLAIS_EMAIL, LEGALLAIS_PASSWORD)
    driver.disable_human_mode()
    driver.get(BASE_URL + "/user/order")
    driver.wait_for_page_to_be(BASE_URL + "/user/order", wait=5)
    # Collecte des commandes sur la fenêtre glissante (7 jours par défaut)
    check_time = check_date(driver)
    Url_cmd = []
    Url_cmd.extend(get_url_cmd(driver))

    # Paginer tant que la dernière date de la page n'est pas antérieure à la semaine
    while check_time != True:
        try:
            next = driver.select(NEXT_PAGE_BUTTON, 0)
            next.scroll_into_view()
            driver.move_mouse_to_element(NEXT_PAGE_BUTTON, 1)
            next.click()

            Url_cmd.extend(get_url_cmd(driver))
            check_time = check_date(driver)
        except Exception as e:
            log_exception(today, e, "érreur de bouclade pour le tab commandes")
            break
    print(f"{len(Url_cmd)} de commande")

    for cmd in Url_cmd:
        try:
            driver.get(BASE_URL + cmd['link'], timeout=10)
            driver.wait_for_page_to_be(BASE_URL + cmd['link'], 3)
            Commande = get_Info(driver, cmd)
            print(f"Écriture de la commande {Commande} en SQLite")
            persist_tracking(Commande)
        except Exception as e:
            log_exception(today, e, f"érreur page {cmd['link']}")
    print(Url_cmd[-1])


if "__main__" == __name__:
    main()
