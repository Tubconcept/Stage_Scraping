"""
Script de suppression des adresses client Prolians (suppradrr).

Se connecte au portail B2B, ouvre la page « Mes adresses » et supprime
les fiches adresse affichées sous forme de cartes, en ignorant les deux
premières cartes (souvent adresse principale / facturation).

Usage : exécution directe ``python scrap_suppradrr.py`` après configuration
des identifiants ``User_P3`` / ``Password_P3`` dans le fichier ``.env``.
"""
import sys
from pathlib import Path

# --- Racine projet (imports auth, selectors) ---
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os
import time
from playwright.sync_api import sync_playwright, Page
from dotenv import load_dotenv
from css_selectors.prolians import Selectors
from auth.prolians.cookie_manager import ensure_logged_in

load_dotenv(PROJECT_ROOT / ".env")

# Identifiants et URL de base du portail Prolians
User = os.getenv("User_P3")
Password = os.getenv("Password_P3")
BASE_URL = Selectors.BASE_URL


# =============================
# GESTION DES POPUPS
# =============================

def anti_popup(page: Page):
    """Ferme la bannière cookies / modale « Tout accepter » si présente."""
    try:
        page.locator(f"xpath={Selectors.accept_all_xpath}").click(timeout=3000)
    except Exception:
        pass


# =============================
# SUPPRESSION DES ADRESSES
# =============================

def suppr_addr(page: Page):
    """
    Parcourt les cartes d'adresse et déclenche la suppression une par une.

    Les deux premières cartes (``divs[2:]``) sont volontairement ignorées
    pour ne pas effacer l'adresse par défaut ou de facturation.
    """
    page.wait_for_timeout(1000)
    anti_popup(page)
    # Chaque adresse est un bloc data-testid='card'
    divs = page.locator("div[data-testid='card']").all()

    for div in divs[2:]:
        page.wait_for_selector("button[aria-label='Supprimer mon adresse']")
        delete_button = div.locator("button[aria-label='Supprimer mon adresse']")

        if delete_button.is_visible():
            delete_button.click()
            # Modale de confirmation Magento / React
            page.wait_for_selector("button[aria-label='Valider']", timeout=5000)

            accept_button = page.locator("button[aria-label='Valider']")

            if accept_button.is_visible():
                accept_button.click()
                # Délai pour laisser l'API supprimer l'adresse côté serveur
                page.wait_for_timeout(4000)
            else:
                print("Bouton de confirmation non visible.")
        else:
            print("Bouton 'Supprimer' introuvable pour une des adresses.")


# =============================
# MAIN
# =============================

def main():
    """Lance Chromium, authentifie l'utilisateur puis exécute la suppression."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        if not ensure_logged_in(page, context, User, Password):
            print("Connexion échouée — arrêt.")
            browser.close()
            return

        page.goto(f"{BASE_URL}/customer/addresses")
        # Attente initiale : chargement React des cartes adresse
        time.sleep(8)
        suppr_addr(page)

        browser.close()


if __name__ == "__main__":
    main()


# ─── Wrapper GUI ───────────────────────────────────────────────────────────────

_stop_flag: bool = False


class ProlianSupprScraper:
    """Wrapper exposant request_stop() pour la GUI."""

    def request_stop(self) -> None:
        global _stop_flag
        _stop_flag = True

    def run(self) -> None:
        global _stop_flag
        _stop_flag = False
        main()


def create_scraper() -> ProlianSupprScraper:
    """Factory attendue par la GUI."""
    return ProlianSupprScraper()
