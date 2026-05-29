from __future__ import annotations
import os
import time
import importlib.util as _ilu
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List
from botasaurus.browser import browser, Driver
from dotenv import load_dotenv

_spec = _ilu.spec_from_file_location("_legallais_css", Path(__file__).resolve().parents[3] / "css_selectors" / "legallais.py")
_mod = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith('_')})
del _ilu, _spec, _mod

load_dotenv()
@dataclass
class AddressItem:
    index: int
    text: str
    delete_selector: Optional[str] = None
    is_default: bool = False

# ---------------------- Configuration ---------------------------------
LOGIN_URL = "https://www.legallais.com/user/connection"
LEGALLAIS_EMAIL=os.getenv("User")
LEGALLAIS_PASSWORD=os.getenv("Password")
# Page "Mes adresses" – à ajuster si besoin après login
ADDRESSES_URL_CANDIDATES = [
    # essaye direct par URL si connue (sinon navigation par menu)
    "https://www.legallais.com/user/my-account",  # hypothétique 
]

DRY_RUN = True  # True = ne clique pas sur "Supprimer", juste un aperçu
HEADLESS = False  # Mettez True pour exécution silencieuse une fois validé
HUMAN_MODE = True  # mouvements humains pour réduire la détection
WAIT = 6  # délai d'attente implicite (secondes)
# ---------------------- Utilitaires Driver -----------------------------
# Sélecteurs à ajuster en fonction du DOM réel
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
    driver.add_cookies([{"name":"CookiesConsent_ads","value":"true","url": "https://www.legallais.com"},
                        {"name":"CookiesConsent_individualCustomization","value":"true","url": "https://www.legallais.com"},
                        {"name":"CookiesConsent_required","value":"1","url": "https://www.legallais.com"}])
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
       
           

def cleanup_addresses(driver: Driver):
    while True:
        # Récupère toutes les cartes
        try:
            card = driver.select(SEL["address_cards"]+":nth-child(3)")
        except:
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

# ---------------------- Tâche principale --------------------------------

@browser(block_images=True, headless=HEADLESS,)
def cleanup_legallais_addresses(driver: Driver, _data=None):

    print("Ouverture de session…")
    _fill_and_submit_login(driver, LEGALLAIS_EMAIL, LEGALLAIS_PASSWORD)

    print("Navigation aux adresses.")
    _navigate_to_addresses(driver)

    print("Collecte et suppresion des adresses")
    cleanup_addresses(driver)

    

    # Re-lister après suppression pour vérification
   


if __name__ == "__main__":
    result = cleanup_legallais_addresses()
    # Botasaurus sauve automatiquement en output/cleanup_legallais_addresses.json
    print("Terminé. Consultez output/cleanup_legallais_addresses.json pour le récap.")
    
    