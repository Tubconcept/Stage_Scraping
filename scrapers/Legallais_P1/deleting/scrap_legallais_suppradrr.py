"""
Script de suppression des adresses de livraison Legallais (site P1).

Rôle :
    Se connecte au compte Legallais, navigue vers « Mes adresses » et supprime
    les adresses au-delà des deux premières (boucle sur la 3ème carte).

Type : suppression (nettoyage des adresses enregistrées).

Architecture :
    Fichier autonome utilisant Botasaurus (@browser) : pas de séparation
    scrap_/scraper_. Réutilise cookie_manager_legallais pour la session et
    les sélecteurs de selectors/legallais.py.

Consommateurs : CLI (__main__ via cleanup_legallais_addresses).
"""

from __future__ import annotations

# ─── Bootstrap et imports ─────────────────────────────────────────────────────

import os
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from botasaurus.browser import browser, Driver
from dotenv import load_dotenv
from css_selectors.legallais import SELECTORS as _LEGALLAIS_SEL

EMAIL_INPUT    = _LEGALLAIS_SEL["email"]
PASSWORD_INPUT = _LEGALLAIS_SEL["password"]
LOGIN_BUTTON   = _LEGALLAIS_SEL["submit"]
BREADCRUMB     = _LEGALLAIS_SEL["breadcrumb"]

load_dotenv()

# ─── Modèle de données (adresse) ────────────────────────────────────────────────

@dataclass
class AddressItem:
    index: int
    text: str
    delete_selector: Optional[str] = None
    is_default: bool = False

# ─── Configuration ────────────────────────────────────────────────────────────

LOGIN_URL = "https://www.legallais.com/user/connection"
LEGALLAIS_EMAIL=os.getenv("User_P1")
LEGALLAIS_PASSWORD=os.getenv("Password_P1")
# Page "Mes adresses" – à ajuster si besoin après login
ADDRESSES_URL_CANDIDATES = [
    # essaye direct par URL si connue (sinon navigation par menu)
    "https://www.legallais.com/user/my-account",  # hypothétique 
]
URL_SITE = "https://www.legallais.com" 

DRY_RUN = True  # True = ne clique pas sur "Supprimer", juste un aperçu
HEADLESS = False  # Mettez True pour exécution silencieuse une fois validé
HUMAN_MODE = True  # mouvements humains pour réduire la détection
WAIT = 6  # délai d'attente implicite (secondes)

# Flag d'arrêt partagé entre la classe wrapper et la fonction Botasaurus
_stop_flag: bool = False
# ─── Sélecteurs DOM adresses ──────────────────────────────────────────────────

SEL = {
    # Liste des adresses + actions
    "address_cards": ".pro-space-myaccount__delivery-item",
    "delete_buttons_in_card": "a.js-account-address-delete",
    "confirm_modal": "button#delete-account-address-popup-submit",
}

def accept_cookies_modal(driver: Driver):
    try:
        driver.add_cookies([{"name":"CookiesConsent_ads","value":"true"},{"name":"CookiesConsent_individualCustomization","value":"true"}])
            # Cliquer sur le bouton "Accepter" 
        driver.click("button.cookies-accept-btn")
            # Sinon cliquer sur "Enregistrer et fermer"
            
        driver.click("button.cookies-save-and-close-btn")
    except Exception:
        pass  # Ignorer si la modal n'apparaît pas

def _wait_for(driver: Driver, css: str, timeout: int = WAIT) -> bool:
    try:
        driver.wait_for_element(css, timeout)
        return True
    except Exception:
        return False


def _click_if_present(driver: Driver, css: str) -> bool:
            driver.click(css)
            return True


def _fill_and_submit_login(driver: Driver, email: str, password: str) -> None:
    assert email and password, "Renseignez LEGALLAIS_EMAIL et LEGALLAIS_PASSWORD"
    driver.enable_human_mode()
    driver.add_cookies([{"name":"CookiesConsent_ads","value":"true","url": URL_SITE},
                        {"name":"CookiesConsent_individualCustomization","value":"true","url": URL_SITE},
                        {"name":"CookiesConsent_required","value":"1","url": URL_SITE}])
    driver.get(LOGIN_URL)
    
    _wait_for(driver, EMAIL_INPUT)
    driver.type(EMAIL_INPUT, email)
    driver.type(PASSWORD_INPUT, password)
    _click_if_present(driver, LOGIN_BUTTON)
    _wait_for(driver, BREADCRUMB)


def _navigate_to_addresses(driver: Driver) -> None:
    # Tente les URLs directes
    for url in ADDRESSES_URL_CANDIDATES:

            driver.get(url)
            driver.wait_for_page_to_be(url)
            if _wait_for(driver, SEL["address_cards"], timeout=6):
                return
       
           

# ─── Boucle de suppression ────────────────────────────────────────────────────

def cleanup_addresses(driver: Driver):
    """Supprime la 3ème adresse en boucle jusqu'à n'en plus avoir que 2."""
    while not _stop_flag:
        # Cible toujours la 3ème carte (nth-child(3)) pour conserver les 2 premières
        try:
            card = driver.select(SEL["address_cards"]+":nth-child(3)")
        except Exception:
            print("nettoyage terminée")
            break
      
        print(card.text)
        # Trouver le bouton "Supprimer" à l'intérieur de cette carte

            # Évite select_inside : on cherche depuis l'élément carte
            # card.wait_for(...) renvoie l'élément enfant quand il apparaît
        card.wait_for_element(SEL["delete_buttons_in_card"])
        delete_btn =card.select(SEL["delete_buttons_in_card"])
        delete_btn.scroll_into_view()
        delete_btn.move_mouse_here()
        delete_btn.click()


        # Confirmer la suppression (popup)

        _wait_for(driver,SEL["confirm_modal"], timeout=2)
        confirm_btn =driver.select(SEL["confirm_modal"])
        confirm_btn.scroll_into_view()
        confirm_btn.click()



        # Attendre le rechargement de la page avant l’itération suivante
        try:
            driver.wait_for_page_to_be("https://www.legallais.com/user/my-account")
        except Exception:
            pass
        time.sleep(8)  # petite marge de sécurité

# ─── Point d'entrée Botasaurus ──────────────────────────────────────────────────

@browser(block_images=True, headless=HEADLESS,)
def cleanup_legallais_addresses(driver: Driver, _data=None):
    from auth.legallais.cookie_manager_legallais import (
        load_cookies_for_driver, save_cookies_from_driver,
    )
    from css_selectors.legallais import BASE_URL

    # Cookies de consentement (toujours injectés)
    driver.add_cookies([
        {"name": "CookiesConsent_ads",                     "value": "true", "url": URL_SITE},
        {"name": "CookiesConsent_individualCustomization",  "value": "true", "url": URL_SITE},
        {"name": "CookiesConsent_required",                "value": "1",    "url": URL_SITE},
    ])

    # Tenter de restaurer la session du jour
    session_restored = False
    if load_cookies_for_driver(driver):
        driver.get(BASE_URL)
        if not driver.is_element_present(EMAIL_INPUT):
            print("[Legallais] Session restaurée — connexion ignorée.")
            session_restored = True
        else:
            print("[Legallais] Session expirée — nouvelle connexion en cours...")

    if not session_restored:
        print("[Legallais] Connexion en cours...")
        _fill_and_submit_login(driver, LEGALLAIS_EMAIL, LEGALLAIS_PASSWORD)
        save_cookies_from_driver(driver)

    print("Navigation aux adresses.")
    _navigate_to_addresses(driver)

    print("Collecte et suppresion des adresses")
    cleanup_addresses(driver)

    

    # Re-lister après suppression pour vérification
   


if __name__ == "__main__":
    result = cleanup_legallais_addresses()
    # Botasaurus sauve automatiquement en output/cleanup_legallais_addresses.json
    print("Terminé. Consultez output/cleanup_legallais_addresses.json pour le récap.")


# ─── Wrapper GUI ───────────────────────────────────────────────────────────────

class LegallaisSupprScraper:
    """Wrapper synchrone exposant request_stop() pour la GUI."""

    def request_stop(self) -> None:
        global _stop_flag
        _stop_flag = True

    def run(self) -> None:
        global _stop_flag
        _stop_flag = False
        cleanup_legallais_addresses()


def create_scraper() -> LegallaisSupprScraper:
    """Factory attendue par la GUI."""
    return LegallaisSupprScraper()
    
    