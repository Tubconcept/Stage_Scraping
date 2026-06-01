"""
Gestion de session Setin — sauvegarde/restauration via storage_state Playwright.

La session est stockée dans playwright_profiles/setin_storage.json.
Contrairement à Prolians, elle n'est pas limitée à une journée : elle reste
valide tant que le site ne l'expire pas.

Utilisation dans chaque scraper :
    from auth.setin.cookie_manager_setin import ensure_logged_in

    # context = browser.new_context(storage_state=str(SESSION_FILE)) si session existante
    if not ensure_logged_in(page, context, USERNAME, PASSWORD):
        sys.exit(1)
"""

import json
from pathlib import Path
from playwright.sync_api import Page, BrowserContext

import sys
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from selectors.setin import LOGIN, BASE_URL
from core.config import PROFILES_DIR, TIMEOUT_LONG, TIMEOUT_MEDIUM

SESSION_FILE = PROFILES_DIR / "setin_storage.json"


def save_session(context: BrowserContext) -> None:
    """Sauvegarde le storage_state (cookies + localStorage) dans le fichier de session."""
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    storage = context.storage_state()
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(storage, f, indent=2)
    print(f"Session sauvegardée : {SESSION_FILE.name}")


def is_session_valid() -> bool:
    """Retourne True si un fichier de session existe."""
    return SESSION_FILE.exists()


def is_logged_in(page: Page) -> bool:
    """Vérifie si le compte Setin est connecté sur la page courante."""
    try:
        return page.locator(LOGIN["user_info"]).count() > 0
    except Exception:
        return False


def login(page: Page, username: str, password: str) -> bool:
    """Effectue la connexion Setin email + mot de passe. Retourne True si réussie."""
    if not username or not password:
        print("Identifiants manquants (User_P5= et Password_P5= dans .env)")
        return False

    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"Impossible de charger le site : {e}")
        return False

    # Attendre la fin du loader
    try:
        page.locator(LOGIN["page_loader"]).wait_for(state="hidden", timeout=10000)
    except Exception:
        pass

    # Passer l'écran d'accueil si présent
    try:
        page.locator(LOGIN["home_return_button"]).first.click(timeout=TIMEOUT_MEDIUM)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass

    if is_logged_in(page):
        print("Déjà connecté")
        return True

    try:
        page.locator(LOGIN["account_icon"]).first.click(timeout=TIMEOUT_LONG)
        page.get_by_placeholder(LOGIN["email_placeholder"]).last.fill(username)
        page.get_by_placeholder(LOGIN["password_placeholder"]).last.fill(password)
        page.locator(LOGIN["submit"]).last.click(timeout=20000)
    except Exception as e:
        print(f"Étape de connexion échouée : {e}")
        return False

    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("load", timeout=20000)
    except Exception:
        pass

    if not is_logged_in(page):
        print("Login échoué — vérifiez les identifiants")
        return False

    print("Login Setin OK")
    return True


def ensure_logged_in(page: Page, context: BrowserContext, username: str, password: str) -> bool:
    """
    Point d'entrée unique pour tous les scrapers Setin sync.

    Priorité 1 : navigue sur le site et vérifie la session en cours → OK
    Priorité 2 : connexion complète email/mot de passe → sauvegarde la session

    Retourne True si connecté, False en cas d'échec.

    Note : pour restaurer une session existante, créer le contexte avec :
        context = browser.new_context(storage_state=str(SESSION_FILE))
    avant d'appeler cette fonction.
    """
    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        try:
            page.locator(LOGIN["page_loader"]).wait_for(state="hidden", timeout=10000)
        except Exception:
            pass
        try:
            page.locator(LOGIN["home_return_button"]).first.click(timeout=TIMEOUT_MEDIUM)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        if is_logged_in(page):
            print("Connexion Setin restaurée (session existante)")
            return True
    except Exception as e:
        print(f"Vérification session échouée : {e}")

    print("Session absente ou expirée — connexion en cours")
    if not login(page, username, password):
        return False

    save_session(context)
    return True
