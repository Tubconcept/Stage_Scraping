"""
Connexion manuelle Prolians — à lancer une seule fois par jour.

Lance un navigateur, se connecte avec le .env, et sauvegarde la session dans
auth/prolians/sessions/. Tous les scrapers Prolians réutilisent cette session.

Usage :
    $env:PYTHONPATH = "."
    python auth/prolians/manual_login.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from auth.prolians.cookie_manager import ensure_logged_in, session_file_for

USERNAME = os.getenv("User_P3", "")
PASSWORD = os.getenv("Password_P3", "")


def main() -> None:
    if not USERNAME or not PASSWORD:
        print("Identifiants manquants dans .env (User_P3 et Password_P3)")
        sys.exit(1)

    session_path = session_file_for()
    if session_path.exists():
        print(f"Session du jour déjà existante : {session_path.relative_to(ROOT)}")
        print("Supprimer le fichier pour forcer une nouvelle connexion.")
        sys.exit(0)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        if ensure_logged_in(page, context, USERNAME, PASSWORD):
            print("\nSession prête — tous les scrapers Prolians peuvent l'utiliser aujourd'hui.")
        else:
            print("\nConnexion échouée. Vérifiez les identifiants dans .env")

        browser.close()


if __name__ == "__main__":
    main()
