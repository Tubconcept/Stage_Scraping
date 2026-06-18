"""
Gestion de session Sonepar — sauvegarde/restauration via storage_state Playwright.

La session est stockée dans playwright_profiles/sonepar/session.json.
Elle reste valide tant que le site ne l'expire pas.

Utilisation dans chaque scraper :
    from auth.sonepar.cookie_manager_sonepar import ensure_logged_in

    # Créer le contexte avec la session existante si disponible :
    # context = browser.new_context(storage_state=str(SESSION_FILE))
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

from css_selectors.sonepar import Selectors
from core.config import PROFILES_DIR

SESSION_FILE = PROFILES_DIR / "sonepar" / "session.json"


def save_session(context: BrowserContext) -> None:
    """Sauvegarde le storage_state (cookies + localStorage) dans le fichier de session."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    storage = context.storage_state()
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(storage, f, indent=2)
    print(f"Session sauvegardée : {SESSION_FILE.name}")


def is_session_valid() -> bool:
    """Retourne True si un fichier de session existe."""
    return SESSION_FILE.exists()


def is_logged_in(page: Page) -> bool:
    """Vérifie si le compte Sonepar est connecté (bouton login absent)."""
    try:
        return page.locator(Selectors.login_button).count() == 0
    except Exception:
        return False


def login(page: Page, username: str, password: str) -> bool:
    """Effectue la connexion Sonepar email + mot de passe. Retourne True si réussie."""
    if not username or not password:
        print("Identifiants manquants (User_P8= et Password_P8= dans .env)")
        return False

    try:
        page.goto(Selectors.BASE_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"Impossible de charger le site : {e}")
        return False

    # Accepter les cookies si présent
    try:
        page.locator(Selectors.cookie_accept_button).click(timeout=3000)
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass

    if is_logged_in(page):
        print("Déjà connecté")
        return True

    try:
        page.locator(Selectors.login_button).click(timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        page.locator(Selectors.email_input).fill(username)
        page.locator(Selectors.password_input).fill(password)
        page.locator(Selectors.submit).click(timeout=10000)
    except Exception as e:
        print(f"Étape de connexion échouée : {e}")
        return False

    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass

    if not is_logged_in(page):
        print("Login échoué — vérifiez les identifiants")
        return False

    print("Login Sonepar OK")
    return True


def ensure_logged_in(
    page: Page, context: BrowserContext, username: str, password: str
) -> bool:
    """
    Point d'entrée unique pour tous les scrapers Sonepar sync.

    Priorité 1 : navigue sur le site et vérifie la session en cours → OK
    Priorité 2 : connexion complète email/mot de passe → sauvegarde la session

    Retourne True si connecté, False en cas d'échec.

    Note : pour restaurer une session existante, créer le contexte avec :
        context = browser.new_context(storage_state=str(SESSION_FILE))
    avant d'appeler cette fonction.
    """
    try:
        page.goto(Selectors.BASE_URL, wait_until="domcontentloaded", timeout=30000)
        try:
            page.locator(Selectors.cookie_accept_button).click(timeout=3000)
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        if is_logged_in(page):
            print("Connexion Sonepar restaurée (session existante)")
            return True
    except Exception as e:
        print(f"Vérification session échouée : {e}")

    print("Session absente ou expirée — connexion en cours")
    if not login(page, username, password):
        return False

    save_session(context)
    return True
