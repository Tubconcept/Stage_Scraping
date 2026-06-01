import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
import os

from playwright.async_api import async_playwright, Page
from dotenv import load_dotenv

import selectors.setin as SEL
from core.config import PROFILES_DIR, TIMEOUT_MEDIUM, TIMEOUT_LONG

load_dotenv(PROJECT_ROOT / ".env")

User = os.getenv("User_P5")
Password = os.getenv("Password_P5")
BASE_URL = SEL.BASE_URL

# URL de la page de gestion des adresses Setin
# TODO: adapter si l'URL exacte est différente
ADDRESSES_URL = f"{BASE_URL}mon-compte/mes-adresses"

# Sélecteurs de la page adresses
# TODO: vérifier ces sélecteurs sur la page réelle de Setin
SEL_ADDRESS_CARD = "div.adresse-bloc"
SEL_DELETE_BUTTON = "a.supprimer-adresse, button.supprimer-adresse"
SEL_CONFIRM_BUTTON = "button.valider-suppression, a.valider-suppression"


# =============================
# CONNEXION
# =============================

async def _is_logged_in(page: Page) -> bool:
    try:
        return await page.locator(SEL.LOGIN["user_info"]).count() > 0
    except Exception:
        return False


async def _connexion(page: Page) -> None:
    await page.locator(SEL.LOGIN["account_icon"]).first.click(timeout=TIMEOUT_LONG)
    await page.get_by_placeholder(SEL.LOGIN["email_placeholder"]).last.fill(User)
    await page.get_by_placeholder(SEL.LOGIN["password_placeholder"]).last.fill(Password)
    await page.locator(SEL.LOGIN["submit"]).last.click(timeout=20000)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("load", timeout=20000)
    except Exception:
        pass
    print("Connexion terminée —", page.url)


# =============================
# SUPPRESSION DES ADRESSES
# =============================

async def suppr_addr(page: Page) -> None:
    await page.wait_for_timeout(1000)

    cards = await page.locator(SEL_ADDRESS_CARD).all()
    print(f"{len(cards)} adresse(s) trouvée(s) — conservation des 2 premières")

    for card in cards[2:]:
        delete_btn = card.locator(SEL_DELETE_BUTTON)

        if not await delete_btn.is_visible():
            print("Bouton 'Supprimer' introuvable pour une adresse — ignorée.")
            continue

        await delete_btn.click()

        try:
            await page.wait_for_selector(SEL_CONFIRM_BUTTON, timeout=5000)
            confirm_btn = page.locator(SEL_CONFIRM_BUTTON)
            if await confirm_btn.is_visible():
                await confirm_btn.click()
                await page.wait_for_timeout(2000)
            else:
                print("Bouton de confirmation non visible.")
        except Exception:
            print("Pas de popup de confirmation — passage à l'adresse suivante.")


# =============================
# MAIN
# =============================

async def main() -> None:
    if not User or not Password:
        print("Erreur : User_P5 ou Password_P5 manquant dans .env")
        return

    storage_path = PROFILES_DIR / "setin_storage.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)

        context_args = {"viewport": {"width": 1920, "height": 1080}}
        if storage_path.exists():
            context_args["storage_state"] = str(storage_path)

        context = await browser.new_context(**context_args)
        page = await context.new_page()

        await page.goto(BASE_URL)
        await page.wait_for_load_state("domcontentloaded")

        try:
            await page.locator(SEL.LOGIN["page_loader"]).wait_for(
                state="hidden", timeout=10000
            )
        except Exception:
            pass

        try:
            await page.locator(SEL.LOGIN["home_return_button"]).first.click(
                timeout=TIMEOUT_MEDIUM
            )
            await page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

        if not await _is_logged_in(page):
            print("Session expirée ou absente — connexion en cours")
            await _connexion(page)
            # Sauvegarder la session pour les prochains runs
            storage = await context.storage_state()
            import json
            PROFILES_DIR.mkdir(parents=True, exist_ok=True)
            with open(storage_path, "w", encoding="utf-8") as f:
                json.dump(storage, f, indent=2)
        else:
            print("Session active — connexion ignorée")

        await page.goto(ADDRESSES_URL)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(2000)

        await suppr_addr(page)

        await browser.close()
        print("Suppression des adresses terminée.")


if __name__ == "__main__":
    asyncio.run(main())
