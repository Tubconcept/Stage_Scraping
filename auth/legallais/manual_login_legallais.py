"""Login Legallais via Botasaurus (simulation humaine) → export session Playwright."""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from botasaurus.browser import browser, Driver
from auth.legallais.cookie_manager_legallais import SESSION_FILE, SESSION_DIR

USERNAME = os.getenv("User_P1") or os.getenv("User")
PASSWORD = os.getenv("Password_P1") or os.getenv("Password")

LOGIN_URL = "https://www.legallais.com/user/connection"
SEL_EMAIL = "input[name='connexion[login]'], input[type='text'], #connection-id"
SEL_PASSWORD = "input[name='connexion[password]'], input[type='password'], #connection-passwd"
SEL_SUBMIT = "button[data-action='components--connection#sendConnection']"
SEL_POST_LOGIN = "ol.c-breadcrumb"


def _save_session_from_driver(driver: Driver) -> None:
    """Extrait les cookies Botasaurus et les sauvegarde au format Playwright storage_state."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    raw_cookies = driver.get_cookies()
    pw_cookies = []
    for c in raw_cookies:
        pw_cookies.append({
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "expires": float(c.get("expires", c.get("expiry", -1))),
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "sameSite": c.get("sameSite", "Lax"),
        })

    storage_state = {"cookies": pw_cookies, "origins": []}
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(storage_state, f, indent=2)
    print(f"Session Legallais sauvegardée : {SESSION_FILE.name}")


@browser(headless=False, block_images=False)
def _do_login(driver: Driver, _data=None):
    if not USERNAME or not PASSWORD:
        print("Identifiants Legallais manquants dans .env (User / User_P1 et Password / Password_P1)")
        return

    driver.enable_human_mode()
    driver.get(LOGIN_URL)

    # Accepter le bandeau cookie via JS (Botasaurus = Selenium, pas de :has-text)
    driver.run_js("""
        const buttons = document.querySelectorAll('button');
        for (const btn of buttons) {
            const t = btn.innerText.trim().toUpperCase();
            if (t === 'ACCEPTER' || t === 'TOUT ACCEPTER' || t === "J'ACCEPTE") {
                btn.click();
                break;
            }
        }
    """)

    # Injecter les cookies de consentement pour ne plus voir le bandeau
    driver.add_cookies([
        {"name": "CookiesConsent_ads", "value": "true", "domain": ".legallais.com", "path": "/"},
        {"name": "CookiesConsent_individualCustomization", "value": "true", "domain": ".legallais.com", "path": "/"},
        {"name": "CookiesConsent_required", "value": "1", "domain": ".legallais.com", "path": "/"},
    ])

    driver.wait_for_element(SEL_EMAIL, 15)

    driver.type(SEL_EMAIL, USERNAME)
    driver.type(SEL_PASSWORD, PASSWORD)
    driver.click(SEL_SUBMIT)

    # Attendre la redirection post-login (URL sort de /user/connection)
    import time
    for _ in range(20):
        time.sleep(1)
        current_url = driver.current_url
        print(f"  URL: {current_url}")
        if "connection" not in current_url:
            print("Login Legallais réussi")
            _save_session_from_driver(driver)
            print(f"\nSession prête — tous les scrapers Legallais peuvent l'utiliser.")
            break
    else:
        print("Échec du login — le site n'a pas redirigé après connexion.")

    driver.disable_human_mode()


if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        print("Identifiants manquants dans .env (User / User_P1 et Password / Password_P1)")
        sys.exit(1)

    if SESSION_FILE.exists():
        print(f"Session existante : {SESSION_FILE.name}")
        answer = input("Forcer une nouvelle connexion ? (o/N) : ").strip().lower()
        if answer != "o":
            print("Connexion annulée.")
            sys.exit(0)
        SESSION_FILE.unlink()
        print("Ancienne session supprimée.")

    _do_login()
