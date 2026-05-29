"""
Connexion manuelle Prolians — à lancer une seule fois par jour.

Lance un navigateur, se connecte avec le .env, et sauvegarde la session.
Tous les scrapers (products, orders, tracking, account) utiliseront cette session.

Usage :
    $env:PYTHONPATH = "."
    python auth/manual_login.py
"""

import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT))

from auth.cookie_manager import ensure_logged_in, _SESSION_FILE

USERNAME = os.getenv("User", "")
PASSWORD = os.getenv("Password", "")


def main():
    if not USERNAME or not PASSWORD:
        print("Identifiants manquants dans .env (User= et Password=)")
        sys.exit(1)

    if _SESSION_FILE.exists():
        print(f"Session du jour déjà existante : {_SESSION_FILE.name}")
        print("Supprimer le fichier pour forcer une nouvelle connexion.")
        sys.exit(0)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        if ensure_logged_in(page, context, USERNAME, PASSWORD):
            print(f"\nSession prête — tous les scrapers peuvent l'utiliser aujourd'hui.")
        else:
            print("\nConnexion échouée. Vérifiez les identifiants dans .env")

        browser.close()


if __name__ == "__main__":
    main()
