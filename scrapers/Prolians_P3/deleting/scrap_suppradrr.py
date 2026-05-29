import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os
import time
from playwright.sync_api import sync_playwright, Page
from datetime import datetime
from dotenv import load_dotenv
from selectors.prolians import Selectors
from auth.prolians.cookie_manager_prolians import ensure_logged_in

load_dotenv(PROJECT_ROOT / ".env")

User = os.getenv("User_P3")
Password = os.getenv("Password_P3")
BASE_URL = Selectors.BASE_URL




# =============================
# GESTION DES POPUPS
# =============================

def anti_popup(page: Page):
    try:
        page.locator(f"xpath={Selectors.accept_all_xpath}").click(timeout=3000)
    except:
        pass


# =============================
# SUPPRESSION DES ADRESSES
# =============================

def supprAddr(page: Page):
    page.wait_for_timeout(1000)
    anti_popup(page)
    divs = page.locator("div[data-testid='card']").all()

    for div in divs[2:]:
        page.wait_for_selector("button[aria-label='Supprimer mon adresse']")
        delete_button = div.locator("button[aria-label='Supprimer mon adresse']")

        if delete_button.is_visible():
            delete_button.click()
            page.wait_for_selector("button[aria-label='Valider']", timeout=5000)

            accept_button = page.locator("button[aria-label='Valider']")

            if accept_button.is_visible():
                accept_button.click()
                page.wait_for_timeout(4000)
            else:
                print("Bouton de confirmation non visible.")
        else:
            print("Bouton 'Supprimer' introuvable pour une des adresses.")


# =============================
# MAIN
# =============================

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        if not ensure_logged_in(page, context, User, Password):
            print("Connexion échouée — arrêt.")
            browser.close()
            return

        page.goto(f"{BASE_URL}/customer/addresses")
        time.sleep(8)
        supprAddr(page)

        browser.close()


if __name__ == "__main__":
    main()
