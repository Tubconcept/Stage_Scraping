"""Gestion de session Legallais et acceptation automatiques des cookies."""

import json
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import BrowserContext, Page

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
SESSION_FILE = ROOT / "playwright_profiles" / "legallais_storage.json"

from css_selectors.legallais import BASE_URL, LOGIN
from core.config import PROFILES_DIR

COOKIE_CONSENT_COOKIES = [
    {"name": "CookiesConsent_ads", "value": "true", "url": BASE_URL.rstrip("/")},
    {"name": "CookiesConsent_individualCustomization", "value": "true", "url": BASE_URL.rstrip("/")},
    {"name": "CookiesConsent_required", "value": "1", "url": BASE_URL.rstrip("/")},
]

COOKIE_BUTTON_SELECTORS = [
    "button#onetrust-accept-btn-handler",
    "button:has-text(\"Tout accepter\")",
    "button:has-text(\"Accepter\")",
    "button:has-text(\"Autoriser tout\")",
    "button:has-text(\"J'accepte\")",
    "button:has-text(\"Valider\")",
]


def save_session(context: BrowserContext) -> None:
    """Sauvegarde le storage_state (cookies + localStorage) dans un fichier JSON."""
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    storage = context.storage_state()
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(storage, f, indent=2)
    print(f"Session Legallais sauvegardée : {SESSION_FILE}")


def is_session_valid() -> bool:
    return SESSION_FILE.exists()


def _click_cookie_button(page: Page) -> bool:
    for selector in COOKIE_BUTTON_SELECTORS:
        try:
            button = page.locator(selector).first
            if button.count() > 0 and button.is_visible():
                button.click(timeout=5000)
                print(f"Cookies acceptés via {selector}")
                return True
        except Exception:
            continue
    return False


def accept_cookies(page: Page) -> None:
    """Tente d'accepter un bandeau de cookies et/ou injecte des cookies de consentement."""
    try:
        if page.url.startswith("http"):
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        if _click_cookie_button(page):
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            return
    except Exception:
        pass

    try:
        # Fallback : recherche manuelle d'un bouton par texte dans les éléments <button>.
        for button in page.locator("button").all():
            try:
                text = button.inner_text().strip().lower()
                if any(keyword in text for keyword in ["tout accepter", "accepter", "autoriser tout", "j'accepte", "valider"]):
                    button.click(timeout=5000)
                    print(f"Cookies acceptés via bouton texte : {text}")
                    return
            except Exception:
                continue
    except Exception:
        pass

    try:
        page.context.add_cookies(COOKIE_CONSENT_COOKIES)
        print("Cookies consentement injectés dans le contexte Legallais")
    except Exception:
        pass


def is_logged_in(page: Page) -> bool:
    try:
        if "/user/" in page.url and "connection" not in page.url:
            return True
        if page.locator("a[href*='/user/my-account']").count() > 0:
            return True
        if page.locator("a[href*='/user/account']").count() > 0:
            return True
        # Formulaire de login spécifique absent = on n'est plus sur la page de connexion
        if (page.locator("input[name='connexion[login]']").count() == 0
                and page.locator(LOGIN["login_button"]).count() == 0):
            return True
    except Exception:
        pass
    return False


def login(page: Page, username: str, password: str) -> bool:
    if not username or not password:
        print("Identifiants Legallais manquants dans .env (User / User_P1 et Password / Password_P1)")
        return False

    # Injecter les cookies de consentement AVANT navigation (comme Botasaurus)
    try:
        page.context.add_cookies(COOKIE_CONSENT_COOKIES)
    except Exception:
        pass

    try:
        page.goto("https://www.legallais.com/user/connection", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("load", timeout=15000)
    except Exception as e:
        print(f"Impossible d'accéder à la page de connexion : {e}")
        return False

    # Fermer le bandeau cookie si encore visible
    _click_cookie_button(page)

    try:
        email_loc = page.locator(LOGIN["email_input"]).first
        email_loc.hover()
        email_loc.type(username, delay=250)

        pwd_loc = page.locator(LOGIN["password_input"]).first
        pwd_loc.hover()
        pwd_loc.type(password, delay=250)

        # Attendre l'icône verte du CAPTCHA (coche verte = aria-checked="true")
        try:
            captcha_frame = page.frame_locator('iframe[title*="reCAPTCHA"]')
            captcha_frame.locator('#recaptcha-anchor[aria-checked="true"]').wait_for(timeout=15000)
            print("CAPTCHA validé")
        except Exception:
            # Pas de reCAPTCHA visible ou déjà validé silencieusement
            page.wait_for_timeout(1500)

        btn = page.locator(LOGIN["login_button"]).first
        btn.hover()
        page.wait_for_timeout(300)
        btn.click(timeout=20000)
    except Exception as e:
        print(f"Erreur pendant la saisie du login Legallais : {e}")
        return False

    # Attendre la redirection post-login
    try:
        page.wait_for_url(lambda url: "connection" not in url, timeout=15000)
    except Exception:
        pass

    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass

    if not is_logged_in(page):
        print("Échec du login Legallais — vérifiez les identifiants ou la page de connexion.")
        return False

    print("Login Legallais réussi")
    return True


def ensure_logged_in(page: Page, context: BrowserContext, username: str, password: str) -> bool:
    try:
        if is_session_valid():
            print("Chargement de la session Legallais existante...")
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            accept_cookies(page)
            if is_logged_in(page):
                print("Session Legallais restaurée")
                return True
            print("Session Legallais existante invalide, tentative de reconnexion...")
    except Exception as e:
        print(f"Impossible de restaurer la session Legallais : {e}")

    if not login(page, username, password):
        return False

    try:
        save_session(context)
    except Exception as e:
        print(f"Impossible de sauvegarder la session Legallais : {e}")

    return True
