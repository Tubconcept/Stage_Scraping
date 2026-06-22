"""
Gestion de session Legallais — une seule connexion par jour.

Stratégie :
  - Fichier de session daté  : playwright_profiles/legallais/session_YYYY-MM-DD.json
  - Format                   : storage_state Playwright (cookies + origins)
  - Résultat                 : un seul login par jour, session réutilisée
    toute la journée

Utilisation dans chaque scraper :
    from auth.legallais.cookie_manager_legallais import ensure_logged_in, get_session_state

    storage = get_session_state()          # chemin du fichier du jour ou None
    context = browser.new_context(storage_state=storage)
    page    = context.new_page()

    if not ensure_logged_in(page, context, USERNAME, PASSWORD):
        sys.exit(1)
"""

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from playwright.sync_api import BrowserContext, Page

load_dotenv()

from css_selectors.legallais import BASE_URL, LOGIN_URL, SELECTORS
from core.config import PROFILES_DIR

# ─── Chemins ──────────────────────────────────────────────────────────────────

SESSION_DIR  = PROFILES_DIR / "legallais"
SESSION_FILE = SESSION_DIR / "session.json"

LOGIN = {
    "email_input":   SELECTORS["email"],
    "password_input": SELECTORS["password"],
    "login_button":  SELECTORS["submit"],
}

COOKIE_CONSENT_COOKIES = [
    {"name": "CookiesConsent_ads",                    "value": "true", "url": BASE_URL},
    {"name": "CookiesConsent_individualCustomization", "value": "true", "url": BASE_URL},
    {"name": "CookiesConsent_required",               "value": "1",    "url": BASE_URL},
]

COOKIE_BUTTON_SELECTORS = [
    "button#onetrust-accept-btn-handler",
    "button:has-text(\"Tout accepter\")",
    "button:has-text(\"Accepter\")",
    "button:has-text(\"Autoriser tout\")",
    "button:has-text(\"J'accepte\")",
    "button:has-text(\"Valider\")",
]


# ─── Gestion du fichier de session ───────────────────────────────────────────

def get_session_state() -> str | None:
    """
    Retourne le chemin du fichier de session sous forme de chaîne,
    ou None si aucune session valide n'existe.
    Utilisé pour créer le contexte Playwright : new_context(storage_state=...).
    """
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("cookies"):
                return str(SESSION_FILE)
        except Exception:
            pass
    return None


def save_session(context: BrowserContext) -> None:
    """Sauvegarde le storage_state (cookies + localStorage) dans le fichier de session."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    storage = context.storage_state()
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(storage, f, indent=2)
    print(f"[Legallais] Session sauvegardée : {SESSION_FILE.name}")


def is_session_valid() -> bool:
    """Retourne True si un fichier de session valide existe."""
    return get_session_state() is not None


# ─── Helpers Botasaurus ───────────────────────────────────────────────────────

def load_cookies_for_driver(driver) -> bool:
    """
    Charge les cookies de la session dans un driver Botasaurus.
    Retourne True si des cookies ont été chargés.
    """
    if not SESSION_FILE.exists():
        return False
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cookies = data.get("cookies", [])
        if not cookies:
            return False
        driver.add_cookies(cookies)
        print(f"[Legallais] {len(cookies)} cookie(s) chargé(s) depuis {SESSION_FILE.name}")
        return True
    except Exception as e:
        print(f"[Legallais] Impossible de charger les cookies : {e}")
        return False


def save_cookies_from_driver(driver) -> None:
    """
    Sauvegarde les cookies d'un driver Botasaurus dans le fichier de session.
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    try:
        raw = driver.get_cookies()
        pw_cookies = [
            {
                "name":     c.get("name", ""),
                "value":    c.get("value", ""),
                "domain":   c.get("domain", ""),
                "path":     c.get("path", "/"),
                "expires":  float(c.get("expires", c.get("expiry", -1))),
                "httpOnly": c.get("httpOnly", False),
                "secure":   c.get("secure", False),
                "sameSite": c.get("sameSite", "Lax"),
            }
            for c in raw
        ]
        storage = {"cookies": pw_cookies, "origins": []}
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(storage, f, indent=2)
        print(f"[Legallais] Session sauvegardée : {SESSION_FILE.name}")
    except Exception as e:
        print(f"[Legallais] Impossible de sauvegarder les cookies : {e}")


# ─── Cookies consentement ─────────────────────────────────────────────────────

def _click_cookie_button(page: Page) -> bool:
    for selector in COOKIE_BUTTON_SELECTORS:
        try:
            button = page.locator(selector).first
            if button.count() > 0 and button.is_visible():
                button.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def accept_cookies(page: Page) -> None:
    """Tente d'accepter le bandeau de cookies."""
    try:
        if page.url.startswith("http"):
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        if _click_cookie_button(page):
            return
    except Exception:
        pass

    try:
        for button in page.locator("button").all():
            try:
                text = button.inner_text().strip().lower()
                if any(k in text for k in ["tout accepter", "accepter", "autoriser tout", "j'accepte", "valider"]):
                    button.click(timeout=5000)
                    return
            except Exception:
                continue
    except Exception:
        pass

    try:
        page.context.add_cookies(COOKIE_CONSENT_COOKIES)
    except Exception:
        pass


# ─── Vérification session ─────────────────────────────────────────────────────

# Lien de déconnexion = signal le plus FIABLE d'une session active. Les anciens
# libellés « Mon compte » ont changé sur le site et ne matchent plus.
LOGOUT_LINK_SELECTOR = "a[href*='logout'], a[href*='deconnexion'], a[href*='disconnect']"
LOGIN_FORM_SELECTOR  = "input[name='connexion[login]'], #connection-id"


def is_logged_in(page: Page) -> bool:
    """Vérifie si le compte Legallais est connecté sur la page courante.

    Signal principal : présence d'un lien de déconnexion. Repli : on est sur un
    espace ``/user/*`` (hors page de connexion) sans formulaire de login affiché.

    On n'utilise PLUS « formulaire de login absent + menu présent » : c'était un
    faux positif (vrai même DÉCONNECTÉ sur la page d'accueil → le scraper sautait
    le login puis échouait sur /user/order).
    """
    try:
        if page.locator(LOGOUT_LINK_SELECTOR).count() > 0:
            return True
        if "/user/" in page.url and "connection" not in page.url:
            return page.locator(LOGIN_FORM_SELECTOR).count() == 0
    except Exception:
        pass
    return False


# ─── Login ────────────────────────────────────────────────────────────────────

def login(page: Page, username: str, password: str) -> bool:
    """Effectue la connexion Legallais. Retourne True si réussie."""
    if not username or not password:
        print("[Legallais] Identifiants manquants dans .env (User_P1 / Password_P1).")
        return False

    try:
        page.context.add_cookies(COOKIE_CONSENT_COOKIES)
    except Exception:
        pass

    print("[Legallais] Connexion au site Legallais...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("load", timeout=15000)
    except Exception as e:
        print(f"[Legallais] Impossible d'accéder à la page de connexion : {e}")
        return False

    _click_cookie_button(page)

    try:
        email_loc = page.locator(LOGIN["email_input"]).first
        email_loc.wait_for(state="visible", timeout=10000)
        email_loc.hover()
        email_loc.type(username, delay=250)

        pwd_loc = page.locator(LOGIN["password_input"]).first
        pwd_loc.hover()
        pwd_loc.type(password, delay=250)

        try:
            captcha_frame = page.frame_locator('iframe[title*="reCAPTCHA"]')
            captcha_frame.locator('#recaptcha-anchor[aria-checked="true"]').wait_for(timeout=15000)
            print("[Legallais] CAPTCHA validé.")
        except Exception:
            page.wait_for_timeout(1500)

        btn = page.locator(LOGIN["login_button"]).first
        btn.hover()
        page.wait_for_timeout(300)
        btn.click(timeout=20000)
    except Exception as e:
        print(f"[Legallais] Erreur pendant la saisie : {e}")
        return False

    try:
        page.wait_for_url(lambda url: "connection" not in url, timeout=15000)
    except Exception:
        pass

    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass

    if not is_logged_in(page):
        print("[Legallais] Échec du login — vérifiez les identifiants.")
        return False

    print("[Legallais] Login réussi.")
    return True


# ─── Point d'entrée principal ─────────────────────────────────────────────────

def ensure_logged_in(page: Page, context: BrowserContext, username: str, password: str) -> bool:
    """
    Point d'entrée unique pour tous les scrapers Legallais.

    Priorité 1 : session du jour déjà chargée via storage_state → vérification → OK
    Priorité 2 : login complet → sauvegarde de la session du jour

    Retourne True si connecté, False en cas d'échec.
    """
    if is_session_valid():
        print("[Legallais] Session du jour trouvée. Vérification en cours...")
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            accept_cookies(page)
            page.wait_for_timeout(1000)
            if is_logged_in(page):
                print("[Legallais] Session Legallais encore valide. Connexion ignorée.")
                return True
            print("[Legallais] Session Legallais expirée. Nouvelle connexion en cours...")
        except Exception as e:
            print(f"[Legallais] Erreur de vérification : {e}")
            print("[Legallais] Nouvelle connexion en cours...")
    else:
        print("[Legallais] Aucun cookie pour aujourd'hui. Connexion en cours...")

    if not login(page, username, password):
        return False

    try:
        save_session(context)
    except Exception as e:
        print(f"[Legallais] Impossible de sauvegarder la session : {e}")

    return True
