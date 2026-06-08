"""
Orchestrateur du scraper suivi / tracking Setin (site P5).

Rôle :
    Parcourt les commandes récentes (fenêtre glissante ou plage de dates),
    extrait les informations de suivi colis (transporteur, lien, poids, reliquat)
    et les enregistre en MariaDB (table tracking de setin.db).

Type : suivi (tracking / expédition).

Architecture :
    - scrap_setin_tracking.py (ce fichier) = orchestrateur : run(), boucle
      pagination, filtrage dates, mapping vers insert_tracking().
    - scraper_setin_tracking.py = moteur CSS : extraction ligne commande,
      détection transporteur depuis l'URL, page détail pour poids/articles.

Consommateurs : GUI (create_scraper), CLI (--date-from, --days).
"""

from __future__ import annotations

# ─── Bootstrap du chemin projet ───────────────────────────────────────────────

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from css_selectors.setin import Selectors
from core.config import PROFILES_DIR, TIMEOUT_MEDIUM
from core.logger import log_exception
from db.mariadb_db import init_site_db, insert_tracking

try:
    from .scraper_setin_tracking import SetinTrackingScraper as _SetinCSS
except ImportError:
    from scraper_setin_tracking import SetinTrackingScraper as _SetinCSS  # type: ignore[no-redef]


# ─── Classe orchestratrice ────────────────────────────────────────────────────

class SetinTrackingScraper(_SetinCSS):
    def __init__(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        csv_path: str | Path | None = None,
    ) -> None:
        super().__init__(date_from=date_from, date_to=date_to)
        self._csv_path = None  # GUI compat — CSV export is on-demand only
        self._db_conn = None  # MariaDB sentinel, initialised in run()

    async def run(self) -> None:
        """Lance le scraping du suivi Setin."""
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

            await self._scrape_all(page)

        except Exception as exc:
            log_exception(self.log, exc, "Erreur fatale run() setin_tracking")
        finally:
            if self._db_conn is not None:
                try:
                    self._db_conn.close()
                except Exception:
                    pass
                self._db_conn = None
            await self.close()

        self.log.info(
            "Setin tracking terminé — %s → %s",
            self._date_from.strftime("%d/%m/%Y"),
            self._date_to.strftime("%d/%m/%Y"),
        )

    # ─── Scraping de la liste (boucle principale et décisions métier) ─────────

    async def _scrape_all(self, page: Page) -> None:
        """Parcourt le backoffice commandes sur les 7 derniers jours."""
        orders_url = Selectors.ORDERS_URL
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
            await page.locator(Selectors.page_loader).wait_for(
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

                if order_date > self._date_to:
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

    # ─── Persistance SQLite ───────────────────────────────────────────────────

    def _save_to_db(self, data: dict) -> None:
        """Enregistre le suivi en MariaDB."""
        row = {
            "id_cmd": data.get("id_cmd", ""),
            "ref_cmd": data.get("ref_cmd", ""),
            "date_cmd": data.get("date_cmd", ""),
            "statut_cmd": data.get("statut_cmd", ""),
            "data_pdt": data.get("data_pdt", ""),
            "Date_Reliquat": data.get("Date_Reliquat") or "",
            "weight_exp": data.get("weight_exp") or "",
            "carrier_exp": data.get("carrier_exp") or "",
            "trackinglink_exp": data.get("trackinglink_exp") or "",
            "tracking_exp": data.get("tracking_exp") or "",
        }
        if self._db_conn is not None:
            try:
                insert_tracking(self._db_conn, "setin", row)
            except Exception as exc:
                self.log.debug("MariaDB tracking ignoré : %s", exc)


# ─── Factory et CLI ───────────────────────────────────────────────────────────

def create_scraper(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    csv_path: str | Path | None = None,
) -> SetinTrackingScraper:
    return SetinTrackingScraper(
        date_from=date_from, date_to=date_to, csv_path=csv_path
    )


def _parse_date(s: str) -> datetime:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Date invalide : {s}")


def main() -> None:
    import asyncio

    parser = argparse.ArgumentParser(description="Scraper tracking Setin → MariaDB")
    parser.add_argument("--date-from", dest="date_from", help="JJ/MM/AAAA")
    parser.add_argument("--date-to", dest="date_to", help="JJ/MM/AAAA")
    parser.add_argument("--days", type=int, default=7, help="Si pas de dates : N derniers jours")
    args = parser.parse_args()

    if args.date_from and args.date_to:
        date_from = _parse_date(args.date_from)
        date_to = _parse_date(args.date_to)
    else:
        date_to = datetime.now()
        date_from = date_to - timedelta(days=args.days)

    scraper = create_scraper(date_from=date_from, date_to=date_to)
    asyncio.run(scraper.run())


if __name__ == "__main__":
    main()
