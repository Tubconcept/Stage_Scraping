"""
Chef d'orchestre du scraper Setin (orders) — point d'entrée officiel.

Ce fichier dirige l'ordre d'exécution en appelant les fonctions CSS de scraper_setin_p5.py.
Il contient run(), _scrape_all_orders(), _save_to_db() et expose create_scraper()
pour les consommateurs externes (GUI, tests, cron).
"""

import asyncio
from datetime import datetime, timedelta

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

import selectors.setin as SEL
from core.config import DB_PATH, PROFILES_DIR, TIMEOUT_MEDIUM
from core.logger import log_exception
from db.database import init_db
from db.models import SetinProduct, SetinOrder

# Fonctionne à la fois en import package (GUI) et en script standalone (CLI)
try:
    from .scraper_setin_orders import SetinOrderScraper as _SetinCSS
except ImportError:
    from scrapers.Setin_P5.orders.scraper_setin_orders import SetinOrderScraper as _SetinCSS  # type: ignore[no-redef]


class SetinOrderScraper(_SetinCSS):
    """Chef d'orchestre — orchestre les appels CSS et gère la persistance."""

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Lance le scraping des commandes Setin."""
        init_db(db_path=DB_PATH, extra_models=[SetinProduct, SetinOrder])

        storage_path = PROFILES_DIR / "setin_storage.json"
        storage_state = str(storage_path) if storage_path.exists() else None

        await self.start_browser(headless=False, storage_state=storage_state)
        page = await self.new_page()

        try:
            await page.goto(SEL.BASE_URL)
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

            if not await self._is_logged_in(page):
                self.log.info("Session expirée ou absente — reconnexion en cours")
                await self._connexion(page)
                await self.save_storage_state(storage_path)
            else:
                self.log.info("Session active — connexion ignorée")

            await self._scrape_all_orders(page)

        except Exception as exc:
            log_exception(self.log, exc, "Erreur fatale run() setin_orders")
        finally:
            await self.close()

        self.log.info(
            "Setin commandes terminé — %s → %s",
            self._date_from.strftime("%d/%m/%Y"),
            self._date_to.strftime("%d/%m/%Y"),
        )

    # ------------------------------------------------------------------
    # Orchestration — scraping de la liste des commandes
    # ------------------------------------------------------------------

    async def _scrape_all_orders(self, page: Page) -> None:
        """Parcourt toutes les pages jusqu'à dépasser date_from."""
        orders_url = f"{SEL.BASE_URL}{SEL.ORDERS['orders_url']}"
        current_page = 1
        total_saved = 0
        total_skipped_date = 0
        total_skipped_parse = 0

        self.log.info(
            "Début scraping commandes — %s → %s",
            self._date_from.strftime("%d/%m/%Y"),
            self._date_to.strftime("%d/%m/%Y"),
        )

        await page.goto(orders_url)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        try:
            await page.locator(SEL.LOGIN["page_loader"]).wait_for(
                state="hidden", timeout=10000
            )
        except Exception:
            pass

        self._order_row_selector = await self._resolve_order_row_selector(page)
        if not self._order_row_selector:
            current_url = page.url
            self.log.warning(
                "Page %d : aucun sélecteur de ligne commande valide trouvé — URL : %s",
                current_page, current_url,
            )
            return

        while current_page <= self._max_pages:
            if self.should_stop():
                self.log.info("Arrêt demandé — page %d", current_page)
                break

            try:
                await page.wait_for_selector(self._order_row_selector, timeout=30_000)
            except PlaywrightTimeout:
                current_url = page.url
                self.log.warning(
                    "Page %d : sélecteur '%s' introuvable — URL : %s",
                    current_page, self._order_row_selector, current_url,
                )
                break

            order_elements = await page.locator(self._order_row_selector).all()
            page_count = len(order_elements)

            if page_count == 0:
                self.log.info("Fin pagination — page %d vide", current_page)
                break

            ui_page = await self._get_ui_page_number(page)
            self.log.info(
                "Page %d (UI: %s) — %d commande(s)",
                current_page, ui_page or "?", page_count,
            )

            page_saved = 0
            stop_by_date = False

            for i, order_el in enumerate(order_elements, 1):
                if self.should_stop():
                    stop_by_date = True
                    break

                order_date = await self._parse_order_date(order_el)
                if order_date is None:
                    total_skipped_parse += 1
                    raw = await self._get_order_date_str(order_el)
                    self.log.warning("[p%d] Date illisible (%s) — commande ignorée", current_page, raw)
                    continue

                # Commande trop récente : on passe sans arrêter
                if order_date > self._date_to:
                    total_skipped_date += 1
                    self.log.debug("[p%d] %s > date_to — ignorée", current_page, order_date.strftime("%d/%m/%Y"))
                    continue

                # Commande trop ancienne : arrêt total
                if order_date < self._date_from:
                    self.log.info(
                        "Seuil bas atteint — commande du %s < %s — arrêt pagination",
                        order_date.strftime("%d/%m/%Y"),
                        self._date_from.strftime("%d/%m/%Y"),
                    )
                    stop_by_date = True
                    break

                try:
                    data = await self._extract_order(page, order_el)
                    if data:
                        self._save_to_db(data)
                        page_saved += 1
                        total_saved += 1
                        self.log.debug(
                            "[p%d] %d/%d — %s (%s) sauvegardée",
                            current_page, i, page_count,
                            data.get("id_cmd"), order_date.strftime("%d/%m/%Y"),
                        )
                except Exception as exc:
                    log_exception(self.log, exc, f"Extraction commande p{current_page} l{i}")

            self.log.info(
                "Page %d terminée — %d/%d sauvegardée(s)",
                current_page, page_saved, page_count,
            )

            if stop_by_date:
                break

            first_id_before = await self._first_order_id(page)
            if not await self._has_next_orders_page(page):
                self.log.info("Fin pagination — dernière page (%d)", current_page)
                break

            if not await self._go_to_next_orders_page(page, first_id_before):
                self.log.warning("Pagination bloquée après page %d — arrêt", current_page)
                break

            current_page += 1

        self.log.info(
            "%d commande(s) sauvegardée(s) — %d ignorée(s) hors plage, "
            "%d date(s) illisible(s), %d page(s)",
            total_saved, total_skipped_date, total_skipped_parse, current_page,
        )

    # ------------------------------------------------------------------
    # Persistance SQLite
    # ------------------------------------------------------------------

    def _save_to_db(self, data: dict) -> None:
        """Insère une commande dans setin_orders — ignorée si id_cmd déjà présent."""
        SetinOrder.insert(
            scraped_at=datetime.now(),
            id_cmd=data["id_cmd"],
            ref_cmd=data["ref_cmd"],
            date_cmd=data["date_cmd"],
            statut_cmd=data["statut_cmd"],
            data_pdt=data["data_pdt"],
        ).execute()


def create_scraper(date_from: datetime, date_to: datetime) -> SetinOrderScraper:
    """Fabrique un SetinOrderScraper — seul point d'accès officiel pour les consommateurs externes."""
    return SetinOrderScraper(date_from=date_from, date_to=date_to)


async def main(days: int = 2) -> None:
    date_to = datetime.now()
    date_from = date_to - timedelta(days=days)
    scraper = create_scraper(date_from=date_from, date_to=date_to)
    await scraper.run()


if __name__ == "__main__":
    asyncio.run(main())
