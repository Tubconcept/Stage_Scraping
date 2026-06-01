"""
Connexion manuelle Setin — à lancer pour créer ou renouveler la session.

Lance un navigateur visible, se connecte avec les identifiants du .env,
et sauvegarde la session dans playwright_profiles/setin_storage.json.
Tous les scrapers Setin utiliseront ensuite cette session automatiquement.

Usage :
    uv run python auth/setin/manual_login_setin.py
"""

import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from auth.setin.cookie_manager_setin import ensure_logged_in, SESSION_FILE

USERNAME = os.getenv("User_P5", "")
PASSWORD = os.getenv("Password_P5", "")


def main():
    if not USERNAME or not PASSWORD:
        print("Identifiants manquants dans .env (User_P5= et Password_P5=)")
        sys.exit(1)

    if SESSION_FILE.exists():
        print(f"Session existante : {SESSION_FILE}")
        answer = input("Forcer une nouvelle connexion ? (o/N) : ").strip().lower()
        if answer != "o":
            print("Connexion annulée.")
            sys.exit(0)
        SESSION_FILE.unlink()
        print("Ancienne session supprimée.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        if ensure_logged_in(page, context, USERNAME, PASSWORD):
            print(f"\nSession prête : {SESSION_FILE}")
            print("Tous les scrapers Setin peuvent l'utiliser.")
        else:
            print("\nConnexion échouée. Vérifiez User_P5 et Password_P5 dans .env")

        browser.close()


if __name__ == "__main__":
    main()
