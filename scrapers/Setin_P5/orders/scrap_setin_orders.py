"""
Orchestrateur du scraper commandes Setin (site P5).

Rôle :
    Point d'entrée pour extraire l'historique des commandes Setin sur une plage
    de dates et les enregistrer en MariaDB.

Type : commandes.

Architecture :
    - scrap_setin_orders.py (ce fichier) = orchestrateur : run(), boucle de
      pagination, filtrage par dates, persistance insert_order().
    - scraper_setin_orders.py = moteur CSS : connexion, extraction d'une ligne
      commande, navigation pagination, parsing des dates.

Consommateurs : GUI (create_scraper), CLI (--date-from / --date-to).
"""

# ─── Bootstrap du chemin projet ───────────────────────────────────────────────

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import asyncio
from datetime import datetime, timedelta

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from css_selectors.setin import Selectors
from core.config import PROFILES_DIR, TIMEOUT_MEDIUM
from core.logger import log_exception
from db.mariadb_db import init_site_db, insert_order

# ─── Import du moteur CSS (compatible package et script standalone) ─────────────

try:
    from .scraper_setin_orders import SetinOrderScraper as _SetinCSS
except ImportError:
    from scrapers.Setin_P5.orders.scraper_setin_orders import SetinOrderScraper as _SetinCSS  # type: ignore[no-redef]

FORMAT_DATE = "%d/%m/%Y"

# ─── Classe orchestratrice ────────────────────────────────────────────────────

class SetinOrderScraper(_SetinCSS):
    """Chef d'orchestre — orchestre les appels CSS et gère la persistance."""

    def __init__(self, date_from: datetime, date_to: datetime, csv_path: str | Path | None = None) -> None:
        super().__init__(date_from, date_to)
        self._csv_path = None  # Compat GUI — export CSV à la demande uniquement
        self._db_conn = None  # connexion MariaDB (sentinel), initialisée dans run()

    # ─── Point d'entrée principal ─────────────────────────────────────────────

    async def run(self) -> None:
        """Lance le scraping des commandes Setin."""
        try:
            self._db_conn = init_site_db("setin")
        except Exception as exc:
            self.log.warning("MariaDB non initialisée : %s", exc)
            self._db_conn = None

        storage_path = PROFILES_DIR / "setin" / "session.json"
        storage_state = str(storage_path) if storage_path.exists() else None

        await self.start_browser(headless=False, storage_state=storage_state)
        page = await self.new_page()

        try:
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
            if self._db_conn is not None:
                try:
                    self._db_conn.close()
                except Exception:
                    pass
                self._db_conn = None
            await self.close()

        self.log.info(
            "Setin commandes terminé — %s → %s",
            self._date_from.strftime(FORMAT_DATE),
            self._date_to.strftime(FORMAT_DATE),
        )

    # ─── Orchestration — scraping de la liste des commandes ───────────────────

    async def _navigate_to_orders(self, page: Page) -> bool:
        """Navigue vers la page commandes et résout le sélecteur de lignes. Retourne False si introuvable."""
        await page.goto(Selectors.ORDERS_URL)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        try:
            await page.locator(Selectors.page_loader).wait_for(state="hidden", timeout=10000)
        except Exception:
            pass
        self._order_row_selector = await self._resolve_order_row_selector(page)
        if not self._order_row_selector:
            self.log.warning(
                "Page 1 : aucun sélecteur de ligne commande valide trouvé — URL : %s",
                page.url,
            )
            return False
        return True

    async def _process_single_order(
        self, page: Page, order_el, current_page: int, i: int, page_count: int
    ) -> str:
        """Traite une ligne commande. Retourne : 'saved', 'skipped_parse', 'skipped_date', 'too_old', 'error'."""
        order_date = await self._parse_order_date(order_el)
        if order_date is None:
            raw = await self._get_order_date_str(order_el)
            self.log.warning("[p%d] Date illisible (%s) — commande ignorée", current_page, raw)
            return "skipped_parse"
        if order_date > self._date_to:
            self.log.debug("[p%d] %s > date_to — ignorée", current_page, order_date.strftime("%d/%m/%Y"))
            return "skipped_date"
        if order_date < self._date_from:
            self.log.info(
                "Seuil bas atteint — commande du %s < %s — arrêt pagination",
                order_date.strftime(FORMAT_DATE),
                self._date_from.strftime(FORMAT_DATE),
            )
            return "too_old"
        try:
            data = await self._extract_order(page, order_el)
            if data:
                self._save_to_db(data)
                self.log.debug(
                    "[p%d] %d/%d — %s (%s) sauvegardée",
                    current_page, i, page_count,
                    data.get("id_cmd"), order_date.strftime(FORMAT_DATE),
                )
                return "saved"
        except Exception as exc:
            log_exception(self.log, exc, f"Extraction commande p{current_page} l{i}")
        return "error"

    async def _process_page_orders(
        self, page: Page, current_page: int, order_elements: list
    ) -> tuple[int, bool, int, int]:
        """Traite toutes les commandes d'une page. Retourne (saved, stop_by_date, skipped_date, skipped_parse)."""
        page_saved = 0
        skipped_date = 0
        skipped_parse = 0
        page_count = len(order_elements)

        for i, order_el in enumerate(order_elements, 1):
            if self.should_stop():
                return page_saved, True, skipped_date, skipped_parse
            status = await self._process_single_order(page, order_el, current_page, i, page_count)
            if status == "saved":
                page_saved += 1
            elif status == "skipped_parse":
                skipped_parse += 1
            elif status == "skipped_date":
                skipped_date += 1
            elif status == "too_old":
                return page_saved, True, skipped_date, skipped_parse

        return page_saved, False, skipped_date, skipped_parse

    async def _scrape_all_orders(self, page: Page) -> None:
        """Parcourt toutes les pages jusqu'à dépasser date_from."""
        current_page = 1
        total_saved = 0
        total_skipped_date = 0
        total_skipped_parse = 0

        self.log.info(
            "Début scraping commandes — %s → %s",
            self._date_from.strftime(FORMAT_DATE),
            self._date_to.strftime(FORMAT_DATE),
        )

        if not await self._navigate_to_orders(page):
            return

        while current_page <= self._max_pages:
            if self.should_stop():
                self.log.info("Arrêt demandé — page %d", current_page)
                break

            try:
                await page.wait_for_selector(self._order_row_selector, timeout=30_000)
            except PlaywrightTimeout:
                self.log.warning(
                    "Page %d : sélecteur '%s' introuvable — URL : %s",
                    current_page, self._order_row_selector, page.url,
                )
                break

            order_elements = await page.locator(self._order_row_selector).all()
            page_count = len(order_elements)

            if page_count == 0:
                self.log.info("Fin pagination — page %d vide", current_page)
                break

            ui_page = await self._get_ui_page_number(page)
            self.log.info("Page %d (UI: %s) — %d commande(s)", current_page, ui_page or "?", page_count)

            page_saved, stop_by_date, skipped_date, skipped_parse = await self._process_page_orders(
                page, current_page, order_elements
            )
            total_saved += page_saved
            total_skipped_date += skipped_date
            total_skipped_parse += skipped_parse

            self.log.info("Page %d terminée — %d/%d sauvegardée(s)", current_page, page_saved, page_count)

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

    # ─── Persistance SQLite ───────────────────────────────────────────────────

    def _save_to_db(self, data: dict) -> None:
        """Enregistre une commande en MariaDB."""
        if self._db_conn is not None:
            try:
                insert_order(self._db_conn, "setin", data)
            except Exception as exc:
                self.log.debug("MariaDB commande ignorée : %s", exc)


# ─── Factory et CLI ───────────────────────────────────────────────────────────

def create_scraper(
    date_from: datetime,
    date_to: datetime,
    csv_path: str | Path | None = None,
) -> SetinOrderScraper:
    return SetinOrderScraper(date_from=date_from, date_to=date_to, csv_path=csv_path)


def _parse_date(s: str) -> datetime:
    for fmt in (FORMAT_DATE, "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Date invalide : {s}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper commandes Setin → MariaDB")
    parser.add_argument("--date-from", dest="date_from", help="JJ/MM/AAAA")
    parser.add_argument("--date-to", dest="date_to", help="JJ/MM/AAAA")
    args = parser.parse_args()

    if args.date_from and args.date_to:
        date_from = _parse_date(args.date_from)
        date_to = _parse_date(args.date_to)
    else:
        while True:
            try:
                date_from_str = (await asyncio.to_thread(input, "Date de début (JJ/MM/AAAA) : ")).strip()
                date_to_str   = (await asyncio.to_thread(input, "Date de fin   (JJ/MM/AAAA) : ")).strip()
                date_from = _parse_date(date_from_str)
                date_to   = _parse_date(date_to_str)
                if date_from > date_to:
                    print("Erreur : la date de début doit être ≤ à la date de fin. Recommencez.")
                    continue
                break
            except ValueError as exc:
                print(f"Format invalide ({exc}). Utilisez JJ/MM/AAAA.")

    scraper = create_scraper(date_from=date_from, date_to=date_to)
    await scraper.run()


if __name__ == "__main__":
    asyncio.run(main())
