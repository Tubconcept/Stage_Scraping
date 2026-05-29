"""
Gestion de session Prolians — une seule connexion par jour pour tous les scrapers.

Utilisation dans chaque scraper :
    from auth.cookie_manager import ensure_logged_in

    # Après context = browser.new_context()
    if not ensure_logged_in(page, context, USERNAME, PASSWORD):
        sys.exit(1)
"""

import json
from datetime import datetime
from pathlib import Path
from playwright.sync_api import Page, BrowserContext
from css_selectors.prolians import Selectors

ROOT = Path(__file__).resolve().parents[1]
_SESSION_FILE = ROOT / "auth" / f"session_{datetime.today().strftime('%Y-%m-%d')}.json"


def save_cookies(context: BrowserContext) -> None:
    """Sauvegarde les cookies du contexte dans le fichier de session du jour."""
    with open(_SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(context.cookies(), f)
    print(f"Session sauvegardée : {_SESSION_FILE.name}")


def load_cookies(context: BrowserContext) -> bool:
    """Charge les cookies du jour si le fichier existe. Retourne True si chargé."""
    if not _SESSION_FILE.exists():
        return False
    with open(_SESSION_FILE, "r", encoding="utf-8") as f:
        context.add_cookies(json.load(f))
    print(f"Session chargée : {_SESSION_FILE.name}")
    return True


def is_logged_in(page: Page) -> bool:
    """Vérifie si le compte Prolians est connecté sur la page courante."""
    try:
        return page.locator(Selectors.logged_in_check).count() > 0
    except:
        return False


def login(page: Page, username: str, password: str) -> bool:
    """Effectue la connexion complète email + mot de passe. Retourne True si réussie."""
    if not username or not password:
        print("Identifiants manquants (User= et Password= dans .env)")
        return False

    print(">> Navigation vers la page de connexion...")
    try:
        page.goto(Selectors.LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"Impossible de charger la page de connexion : {e}")
        return False

    page.wait_for_timeout(1500)

    for sel in [Selectors.cookie_acceptt, Selectors.cookie_accept]:
        try:
            if page.locator(sel).count() > 0:
                page.locator(sel).click(timeout=3000)
                page.wait_for_timeout(500)
                break
        except:
            pass

    try:
        email_input = page.locator(Selectors.email_input)
        email_input.wait_for(state="visible", timeout=10000)
        email_input.fill(username)
        page.locator(Selectors.email_button).click()
        page.wait_for_timeout(1500)
    except Exception as e:
        print(f"Étape email échouée : {e}")
        return False

    try:
        pwd_input = page.locator(Selectors.password_input)
        pwd_input.wait_for(state="visible", timeout=10000)
        pwd_input.fill(password)
        page.locator(Selectors.submit_button).click()
        page.wait_for_load_state("domcontentloaded", timeout=25000)
    except Exception as e:
        print(f"Étape mot de passe échouée : {e}")
        return False

    page.wait_for_timeout(2000)
    if "/login" in page.url:
        print("Login échoué — toujours sur la page de connexion")
        return False

    print("Login OK")
    return True


def ensure_logged_in(page: Page, context: BrowserContext, username: str, password: str) -> bool:
    """
    Point d'entrée unique pour tous les scrapers Prolians.

    Priorité 1 : charge les cookies du jour → vérifie la connexion → OK
    Priorité 2 : connexion complète email/mot de passe → sauvegarde les cookies

    Retourne True si connecté, False en cas d'échec.
    """
    if load_cookies(context):
        try:
            page.goto(Selectors.BASE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
            if is_logged_in(page):
                print("Connexion restaurée (session du jour)")
                return True
        except Exception as e:
            print(f"Vérification session échouée : {e}")
        print("Session expirée — reconnexion en cours")

    if not login(page, username, password):
        return False

    save_cookies(context)
    return True
