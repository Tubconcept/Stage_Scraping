"""
Script de suppression des adresses de livraison Setin (site P5).

Rôle :
    Se connecte au compte Setin, accède à la page « Mes adresses » et supprime
    les adresses au-delà des deux premières conservées (boucle tant qu'il reste
    plus de 2 cartes d'adresse).

Type : suppression (nettoyage des adresses enregistrées).

Architecture :
    Fichier autonome (pas de séparation scrap_/scraper_) : connexion Playwright,
    interaction DOM et point d'entrée CLI/GUI dans le même module.
    Expose SetinSupprScraper + create_scraper() pour l'intégration GUI,
    avec support du flag d'arrêt request_stop().

Consommateurs : CLI (__main__), GUI (create_scraper).
"""

# ─── Bootstrap du chemin projet ───────────────────────────────────────────────

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ─── Imports ──────────────────────────────────────────────────────────────────

import asyncio
import json
import os

from playwright.async_api import async_playwright, Page
from dotenv import load_dotenv

from css_selectors.setin import Selectors
from core.config import PROFILES_DIR, TIMEOUT_MEDIUM, TIMEOUT_LONG

load_dotenv(PROJECT_ROOT / ".env")

# Identifiants compte Setin (variables d'environnement User_P5 / Password_P5)
User = os.getenv("User_P5")
Password = os.getenv("Password_P5")


# ─── Connexion / vérification de session ──────────────────────────────────────

async def _is_logged_in(page: Page) -> bool:
    try:
        return await page.locator(Selectors.user_info).count() > 0
    except Exception:
        return False


async def _connexion(page: Page) -> None:
    await page.locator(Selectors.account_icon).first.click(timeout=TIMEOUT_LONG)
    await page.get_by_placeholder(Selectors.email_placeholder).last.fill(User)
    await page.get_by_placeholder(Selectors.password_placeholder).last.fill(Password)
    await page.locator(Selectors.submit).last.click(timeout=20000)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("load", timeout=20000)
    except Exception:
        pass
    print("Connexion terminée —", page.url)


# ─── Suppression des adresses ─────────────────────────────────────────────────

async def suppr_addr(page: Page, should_stop=None) -> None:
    """Supprime les adresses une par une tant qu'il en reste plus de 2."""
    await page.wait_for_timeout(1000)

    while True:
        # Vérification du flag d'arrêt (utilisé par le wrapper GUI)
        if should_stop and should_stop():
            print("Arrêt demandé — suppression interrompue.")
            break

        cards = await page.locator(Selectors.address_card).all()
        total = len(cards)

        # Règle métier : conserver au minimum les 2 premières adresses
        if total <= 2:
            print(f"{total} adresse(s) restante(s) — conservation des 2 premières, terminé.")
            break

        # Toujours supprimer la 3ème carte (index 2) pour préserver les 2 premières
        print(f"{total} adresse(s) trouvée(s) — suppression de la 3ème...")
        delete_btn = page.locator(Selectors.address_card).nth(2).locator(Selectors.address_delete)

        if not await delete_btn.is_visible():
            print("Bouton 'Supprimer' introuvable — arrêt.")
            break

        # Accepter automatiquement la boîte de dialogue de confirmation native
        page.once("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await delete_btn.click()
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1000)


# ─── Point d'entrée CLI ───────────────────────────────────────────────────────

async def main() -> None:
    if not User or not Password:
        print("Erreur : User_P5 ou Password_P5 manquant dans .env")
        return

    storage_path = PROFILES_DIR / "setin" / "session.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)

        context_args = {"viewport": {"width": 1920, "height": 1080}}
        if storage_path.exists():
            context_args["storage_state"] = str(storage_path)

        context = await browser.new_context(**context_args)
        page = await context.new_page()

        await page.goto(Selectors.BASE_URL)
        await page.wait_for_load_state("domcontentloaded")

        try:
            await page.locator(Selectors.page_loader).wait_for(
                state="hidden", timeout=10000
            )
        except Exception:
            pass

        try:
            await page.locator(Selectors.home_return_button).first.click(
                timeout=TIMEOUT_MEDIUM
            )
            await page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

        if not await _is_logged_in(page):
            print("Session expirée ou absente — connexion en cours")
            await _connexion(page)
            PROFILES_DIR.mkdir(parents=True, exist_ok=True)
            with open(storage_path, "w", encoding="utf-8") as f:
                json.dump(await context.storage_state(), f, indent=2)
        else:
            print("Session active — connexion ignorée")

        await page.goto(Selectors.ADDRESSES_URL)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)

        await suppr_addr(page)

        await browser.close()
        print("Suppression des adresses terminée.")


if __name__ == "__main__":
    asyncio.run(main())


# ─── Wrapper GUI (même logique que main, avec arrêt demandé) ───────────────────

class SetinSupprScraper:
    """Wrapper GUI avec support d'arrêt — même pattern que les autres scrapers Setin."""

    def __init__(self):
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    async def run(self) -> None:
        if not User or not Password:
            print("Erreur : User_P5 ou Password_P5 manquant dans .env")
            return

        storage_path = PROFILES_DIR / "setin" / "session.json"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context_args = {"viewport": {"width": 1920, "height": 1080}}
            if storage_path.exists():
                context_args["storage_state"] = str(storage_path)
            context = await browser.new_context(**context_args)
            page    = await context.new_page()

            await page.goto(Selectors.BASE_URL)
            await page.wait_for_load_state("domcontentloaded")
            try:
                await page.locator(Selectors.page_loader).wait_for(state="hidden", timeout=10000)
            except Exception:
                pass
            try:
                await page.locator(Selectors.home_return_button).first.click(timeout=TIMEOUT_MEDIUM)
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass

            if not await _is_logged_in(page):
                await _connexion(page)
                PROFILES_DIR.mkdir(parents=True, exist_ok=True)
                with open(storage_path, "w", encoding="utf-8") as f:
                    json.dump(await context.storage_state(), f, indent=2)

            await page.goto(Selectors.ADDRESSES_URL)
            await page.wait_for_load_state("domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)

            await suppr_addr(page, should_stop=lambda: self._stop_requested)

            await browser.close()


def create_scraper() -> SetinSupprScraper:
    return SetinSupprScraper()
