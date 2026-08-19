"""
Orchestrateur du scraper produits Setin (site P5).

Rôle :
    Point d'entrée officiel pour l'extraction du catalogue produits Setin.
    Ce fichier coordonne le navigateur Playwright, la session utilisateur,
    la boucle de scraping et la persistance en base SQLite (setin.db).

Type : produits (catalogue ou dérivé des commandes).

Architecture :
    - scrap_setin_products.py (ce fichier) = orchestrateur : run(), persistance,
      gestion session, pagination métier, reprise sur crash.
    - scraper_setin_products.py = moteur CSS : sélecteurs, navigation catégories,
      extraction HTML des fiches produit et variantes.
    Hérite de SetinProductScraper (CSS) et SetinOrderScraper (mode dates)
    pour réutiliser la collecte d'URLs via l'historique commandes.

Consommateurs : GUI (create_scraper), CLI (--category, --date-from/--date-to).
"""

# ─── Bootstrap du chemin projet ───────────────────────────────────────────────

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ─── Imports standards et métier ──────────────────────────────────────────────

import argparse
import asyncio
import os
from datetime import datetime

from css_selectors.setin import Selectors
from core.config import PROFILES_DIR, TIMEOUT_MEDIUM, TIMEOUT_PAGE_LOAD
from core.base_scraper import BaseScraper
from core.logger import log_exception
from db.mariadb_db import init_site_db, insert_product, get_scraped_product_urls, resolve_decli_index
try:
    from .scraper_setin_products import SetinProductScraper as _SetinCSS
    from ..orders.scraper_setin_orders import SetinOrderScraper as _OrderCSS
except ImportError:
    from scraper_setin_products import SetinProductScraper as _SetinCSS  # type: ignore[no-redef]
    from scrapers.Setin_P5.orders.scraper_setin_orders import SetinOrderScraper as _OrderCSS  # type: ignore[no-redef]


# ─── Classe orchestratrice ────────────────────────────────────────────────────

class SetinProductScraper(_SetinCSS, _OrderCSS):
    """Orchestration produits + persistance SQLite.

    Héritage multiple :
        - _SetinCSS : extraction catalogue (catégories, fiches, variantes).
        - _OrderCSS : collecte d'URLs produit via les commandes (mode dates).
    """

    def __init__(
        self,
        category_name: str = "",
        csv_path: str | Path | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> None:
        BaseScraper.__init__(self, "setin_products")
        self._username = os.getenv("User_P5", "")
        self._password = os.getenv("Password_P5", "")
        self._category_name = category_name or Selectors.CATEGORY_NAMES[0]
        if not self._username or not self._password:
            self.log.warning("User_P5 ou Password_P5 non défini dans .env")
        self._csv_path: Path | None = None  # Compat GUI — export CSV à la demande uniquement
        self._db_conn = None  # connexion MariaDB (sentinel), initialisée dans run()
        # Compteur porté par l'instance : un compteur local à run() reste à 0 si une
        # exception interrompt le mode, et le run annonce alors « 0 produit » alors
        # que des lignes SONT en base — exactement le symptôme qui fait croire à un échec.
        self._rows_written = 0
        # Mode « dates » : les URLs viennent des commandes, pas du menu catalogue
        self._use_order_dates = date_from is not None and date_to is not None
        if self._use_order_dates:
            self._date_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0)  # type: ignore[union-attr]
            self._date_to = date_to.replace(hour=23, minute=59, second=59, microsecond=0)  # type: ignore[union-attr]
            self._max_pages = 500
            self._order_row_selector = Selectors.order_row

    # ─── Persistance SQLite ───────────────────────────────────────────────────

    def _persist_product(
        self,
        produit: dict,
        cat1: str,
        cat2: str,
        cat3: str,
        source_url: str,
    ) -> None:
        # Conversion dict interne → colonnes standardisées CSV_HEADERS puis INSERT
        row = self.to_csv_row(produit, cat1, cat2, cat3, source_url)
        if self._db_conn is not None:
            try:
                insert_product(self._db_conn, "setin", row)
            except Exception as exc:
                self.log.debug("MariaDB produit ignoré : %s", exc)

    # ─── Helpers partagés ────────────────────────────────────────────────────

    def _apply_combination_index(self, products: list[dict]) -> None:
        """Assigne IndexCombination aux variantes d'un produit combiné."""
        if not products:
            return
        parent_ref = products[0].get("parent", "") or ""
        try:
            grp_idx = resolve_decli_index("setin", parent_ref)
            for _p in products:
                if _p.get("IsCombination"):
                    _p["IndexCombination"] = grp_idx
        except Exception:
            pass

    async def _scrape_and_persist(self, page, link: str) -> int:
        """Extrait un produit, applique l'index variante et persiste. Retourne le nombre de lignes écrites."""
        has_combo, products = await self._get_product_data(page, link)
        if has_combo and products:
            self._apply_combination_index(products)
        cat1, cat2, cat3 = await self._ariane(page)
        for produit in products:
            self._persist_product(produit, cat1, cat2, cat3, link)
        self._rows_written += len(products)
        return len(products)

    async def _ensure_session(self, page, storage_path) -> None:
        """Navigue vers la page d'accueil, attend le chargement et reconnecte si besoin."""
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
        if not await self._is_logged_in(page):
            self.log.info("Session expirée — reconnexion")
            await self._connexion(page)
            await self.save_storage_state(storage_path)
        else:
            self.log.info("Session active")

    def _load_seen_urls(self) -> set[str]:
        """Charge les URLs déjà scrappées depuis la DB pour reprendre un scraping interrompu."""
        seen: set[str] = (
            get_scraped_product_urls(self._db_conn, "setin")
            if self._db_conn is not None else set()
        )
        if seen:
            self.log.info("Reprise — %d URL(s) déjà scrappée(s) ignorées", len(seen))
        return seen

    # ─── Mode dates ──────────────────────────────────────────────────────────

    async def _run_date_mode(self, page, seen: set[str], limit_products: int | None) -> int:
        """Scrape les produits issus de l'historique commandes. Retourne le nombre de lignes écrites."""
        product_links = await self._collect_product_urls_from_orders(page)
        self.log.info("Mode dates : %d produit(s) à scraper", len(product_links))
        rows_written = 0
        for link in product_links:
            if self.should_stop():
                break
            if limit_products is not None and rows_written >= limit_products:
                break
            if link in seen:
                continue
            seen.add(link)
            try:
                rows_written += await self._scrape_and_persist(page, link)
            except Exception as exc:
                log_exception(self.log, exc, f"Produit {link}")
        return rows_written

    # ─── Mode catalogue ───────────────────────────────────────────────────────

    async def _collect_slice_links(self, page, cat_url: str, seen: set[str]) -> list[str]:
        """Collecte les liens produits visibles sur la tranche courante (non encore traités)."""
        page_links: list[str] = []
        try:
            await page.wait_for_selector("div.product_box", timeout=5000)
            for box in await page.locator(Selectors.product_box_link).element_handles():
                lnk = await box.get_attribute("href")
                if lnk and lnk not in seen:
                    page_links.append(lnk)
                    seen.add(lnk)
        except Exception as exc:
            log_exception(self.log, exc, f"Liens {cat_url}")
        return page_links

    async def _advance_to_next_slice(self, page) -> bool:
        """Clique sur 'charger plus'. Retourne False si le bouton est absent ou si une erreur survient."""
        try:
            next_btn = page.locator(Selectors.pagination_next)
            if await next_btn.count() == 0:
                return False
            await next_btn.scroll_into_view_if_needed()
            await next_btn.click()
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(1000)
            return True
        except Exception:
            return False

    async def _process_slice_links(
        self, product_page, page_links: list[str], limit_products: int | None, rows_written: int
    ) -> int:
        """Scrape chaque lien d'une tranche. Retourne le total de lignes écrites (cumulatif)."""
        for link in page_links:
            if self.should_stop():
                break
            if limit_products is not None and rows_written >= limit_products:
                break
            try:
                rows_written += await self._scrape_and_persist(product_page, link)
            except Exception as exc:
                log_exception(self.log, exc, f"Produit {link}")
        return rows_written

    async def _run_catalogue_category(
        self, page, product_page, cat_url: str, seen: set[str],
        limit_products: int | None, rows_written: int,
    ) -> int:
        """Traite une catégorie (redirection directe ou pagination tranche par tranche). Retourne le total cumulatif."""
        # `wait_until="load"` (défaut) attend jusqu'aux images : les pages Setin
        # dépassent régulièrement les 30 s. Une catégorie injoignable est journalisée
        # et sautée — sans ce garde-fou elle emportait les 35 autres avec elle.
        try:
            await page.goto(cat_url, wait_until="domcontentloaded", timeout=TIMEOUT_PAGE_LOAD)
        except Exception as exc:
            log_exception(self.log, exc, f"Catégorie {cat_url} — sautée")
            return rows_written

        if page.url != cat_url:
            link = page.url
            if link not in seen:
                seen.add(link)
                try:
                    rows_written += await self._scrape_and_persist(product_page, link)
                except Exception as exc:
                    log_exception(self.log, exc, f"Produit {link}")
            return rows_written

        while not self.should_stop():
            if limit_products is not None and rows_written >= limit_products:
                break
            page_links = await self._collect_slice_links(page, cat_url, seen)
            rows_written = await self._process_slice_links(product_page, page_links, limit_products, rows_written)
            if not await self._advance_to_next_slice(page):
                break

        return rows_written

    async def _run_catalogue_mode(self, page, seen: set[str], limit_products: int | None) -> int:
        """Scrape le catalogue catégorie par catégorie. Retourne le nombre total de lignes écrites."""
        cat_urls = await self._get_categories(page, self._category_name)
        self.log.info("Mode catalogue : %d sous-catégorie(s)", len(cat_urls))
        product_page = await self.new_page()
        rows_written = 0

        for cat_url in cat_urls:
            if self.should_stop():
                break
            rows_written = await self._run_catalogue_category(
                page, product_page, cat_url, seen, limit_products, rows_written
            )

        return rows_written

    # ─── Point d'entrée principal ─────────────────────────────────────────────

    async def run(self, limit_products: int | None = None) -> None:
        """Lance le scraping : session → connexion → catalogue ou mode dates → MariaDB."""
        try:
            self._db_conn = init_site_db("setin")
        except Exception as exc:
            self.log.warning("Base MariaDB non initialisée : %s", exc)
            self._db_conn = None

        storage_path = PROFILES_DIR / "setin" / "session.json"
        storage_state = str(storage_path) if storage_path.exists() else None

        await self.start_browser(headless=False, storage_state=storage_state)
        page = await self.new_page()

        try:
            await self._ensure_session(page, storage_path)
            seen = self._load_seen_urls()
            if self._use_order_dates:
                await self._run_date_mode(page, seen, limit_products)
            else:
                await self._run_catalogue_mode(page, seen, limit_products)
        except Exception as exc:
            log_exception(self.log, exc, "run() setin_products")
        finally:
            if self._db_conn is not None:
                try:
                    self._db_conn.close()
                except Exception:
                    pass
                self._db_conn = None
            await self.close()

        self.log.info("Terminé — %d produit(s) enregistré(s) en MariaDB", self._rows_written)


# ─── Factory et CLI ───────────────────────────────────────────────────────────

def create_scraper(
    category_name: str = "",
    csv_path: str | Path | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> SetinProductScraper:
    return SetinProductScraper(
        category_name=category_name,
        csv_path=csv_path,
        date_from=date_from,
        date_to=date_to,
    )


def _parse_date(s: str) -> datetime:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Date invalide : {s} (attendu JJ/MM/AAAA ou AAAA-MM-JJ)")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper produits Setin → MariaDB")
    parser.add_argument("--category", default="", help="Catégorie niveau 1 (mode catalogue)")
    parser.add_argument("--date-from", dest="date_from", help="JJ/MM/AAAA (mode commandes)")
    parser.add_argument("--date-to", dest="date_to", help="JJ/MM/AAAA (mode commandes)")
    parser.add_argument("--limit", type=int, default=None, help="Limite de variantes")
    args = parser.parse_args()

    date_from = _parse_date(args.date_from) if args.date_from else None
    date_to = _parse_date(args.date_to) if args.date_to else None
    if (date_from is None) ^ (date_to is None):
        parser.error("--date-from et --date-to doivent être fournis ensemble")

    limit = args.limit
    if limit is None and os.getenv("SETIN_PRODUCT_LIMIT", "").isdigit():
        limit = int(os.getenv("SETIN_PRODUCT_LIMIT", ""))

    scraper = create_scraper(
        category_name=args.category,
        date_from=date_from,
        date_to=date_to,
    )
    await scraper.run(limit_products=limit)


if __name__ == "__main__":
    asyncio.run(main())
