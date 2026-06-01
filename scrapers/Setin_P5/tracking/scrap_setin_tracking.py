"""Orchestrateur pour le scraping tracking Setin.

Contient : run(), boucle principale, persistance SQLite, factory et CLI.
"""

from __future__ import annotations

from datetime import datetime

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

import selectors.setin as SEL
from core.config import DB_PATH, PROFILES_DIR, TIMEOUT_MEDIUM
from core.logger import log_exception
from db.database import init_db
from db.models import SetinProduct, SetinTracking

try:
    from .scraper_setin_tracking import SetinTrackingScraper as _SetinCSS
except ImportError:
    from scraper_setin_tracking import SetinTrackingScraper as _SetinCSS  # type: ignore[no-redef]


class SetinTrackingScraper(_SetinCSS):
    async def run(self) -> None:
        """Lance le scraping du suivi Setin."""
        init_db(db_path=DB_PATH, extra_models=[SetinProduct, SetinTracking])

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

            await self._scrape_all(page)

        except Exception as exc:
            log_exception(self.log, exc, "Erreur fatale run() setin_tracking")
        finally:
            await self.close()

        self.log.info("Setin tracking terminé — %d dernier(s) jour(s)", self.DAYS)

    # ------------------------------------------------------------------
    # Scraping de la liste (boucle principale et décisions métier)
    # ------------------------------------------------------------------

    async def _scrape_all(self, page: Page) -> None:
        """Parcourt le backoffice commandes sur les 7 derniers jours."""
        orders_url = f"{SEL.BASE_URL}{SEL.ORDERS['orders_url']}"
        current_page = 1
        total_saved = 0
        total_skipped_parse = 0

        self.log.info(
            "Début scraping tracking — fenêtre %d jours, seuil < %s",
            self.DAYS,
            self._date_from.strftime("%d/%m/%Y"),
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
                    self.log.warning("[p%d] Date illisible (%s) — ignorée", current_page, raw)
                    continue

                if order_date < self._date_from:
                    self.log.info(
                        "Seuil atteint — commande du %s < %s — arrêt",
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
                except Exception as exc:
                    log_exception(self.log, exc, f"Extraction tracking p{current_page} l{i}")

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
            "%d entrée(s) sauvegardée(s) — %d date(s) illisible(s), %d page(s)",
            total_saved, total_skipped_parse, current_page,
        )

    # ------------------------------------------------------------------
    # Persistance SQLite
    # ------------------------------------------------------------------

    def _save_to_db(self, data: dict) -> None:
        """Insère une entrée dans setin_tracking — ignorée si id_cmd déjà présent."""
        SetinTracking.insert(
            scraped_at=datetime.now(),
            id_cmd=data["id_cmd"],
            ref_cmd=data["ref_cmd"],
            date_cmd=data["date_cmd"],
            statut_cmd=data["statut_cmd"],
            data_pdt=data["data_pdt"],
            Date_Reliquat=data["Date_Reliquat"],
            weight_exp=data["weight_exp"],
            carrier_exp=data["carrier_exp"],
            trackinglink_exp=data["trackinglink_exp"],
            tracking_exp=data["tracking_exp"],
        ).execute()


def create_scraper() -> SetinTrackingScraper:
    return SetinTrackingScraper()


def main() -> None:
    import asyncio

    scraper = create_scraper()
    asyncio.run(scraper.run())


if __name__ == "__main__":
    main()
