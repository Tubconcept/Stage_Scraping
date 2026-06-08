"""
Gestion de session Prolians — sauvegarde/restauration via cookies Playwright.

La session est stockée dans playwright_profiles/prolians/session.json.
Elle est mise à jour à chaque nouvelle connexion.

Utilisation dans chaque scraper :
    from auth.prolians.cookie_manager import ensure_logged_in

    # Après context = browser.new_context()
    if not ensure_logged_in(page, context, USERNAME, PASSWORD):
        sys.exit(1)
"""

import json
from pathlib import Path

from playwright.sync_api import BrowserContext, Page

import sys as _sys
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

from css_selectors.prolians import Selectors
from core.config import PROFILES_DIR

SESSION_DIR  = PROFILES_DIR / "prolians"
SESSION_FILE = SESSION_DIR / "session.json"


def session_file_for() -> Path:
    """Retourne le chemin du fichier de session Prolians."""
    return SESSION_FILE


def _ensure_session_dir() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def save_cookies(context: BrowserContext) -> None:
    """Sauvegarde les cookies du contexte dans le fichier de session."""
    _ensure_session_dir()
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(context.cookies(), f)
    print(f"Session Prolians sauvegardée : {SESSION_FILE.name}")


def load_cookies(context: BrowserContext) -> bool:
    """Charge les cookies si le fichier existe. Retourne True si chargé."""
    if not SESSION_FILE.is_file():
        return False
    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        context.add_cookies(json.load(f))
    print(f"Session Prolians chargée : {SESSION_FILE.name}")
    return True


def is_logged_in(page: Page) -> bool:
    """Vérifie si le compte Prolians est connecté sur la page courante."""
    try:
        return page.locator(Selectors.logged_in_check).count() > 0
    except Exception:
        return False


def login(page: Page, username: str, password: str) -> bool:
    """Effectue la connexion complète email + mot de passe. Retourne True si réussie."""
    if not username or not password:
        print("Identifiants manquants (User_P3 et Password_P3 dans .env)")
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
        except Exception:
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

    print("Login Prolians OK")
    return True


def ensure_logged_in(page: Page, context: BrowserContext, username: str, password: str) -> bool:
    """
    Point d'entrée unique pour tous les scrapers Prolians.

    Priorité 1 : charge les cookies → vérifie la connexion → OK
    Priorité 2 : connexion complète email/mot de passe → sauvegarde les cookies

    Retourne True si connecté, False en cas d'échec.
    """
    if load_cookies(context):
        try:
            page.goto(Selectors.BASE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
            if is_logged_in(page):
                print("Connexion Prolians restaurée (session existante)")
                return True
        except Exception as e:
            print(f"Vérification session échouée : {e}")
        print("Session expirée — reconnexion en cours")

    if not login(page, username, password):
        return False

    save_cookies(context)
    return True
