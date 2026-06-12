"""
Moteur CSS du scraper suivi / tracking Setin (site P5).

Rôle :
    Extraction des champs de suivi depuis la liste commandes et la page détail :
    transporteur (détection par domaine d'URL), numéro de colis, lien de suivi,
    date reliquat, poids d'expédition et données article (ref:titre:qty).

Type : suivi (tracking).

Architecture :
    - scraper_setin_tracking.py (ce fichier) = couche CSS / parsing.
    - scrap_setin_tracking.py = orchestrateur (run, boucle, SQLite).
    Réutilise la même logique de pagination que le scraper commandes.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os
import re
from datetime import datetime, timedelta

from dotenv import load_dotenv
from playwright.async_api import Page

from css_selectors.setin import Selectors
from core.base_scraper import BaseScraper
from core.config import TIMEOUT_LONG
from core.logger import log_exception

load_dotenv()


# ─── Détection de transporteurs depuis l'URL de suivi ─────────────────────────

_CARRIERS: list[dict] = [
    {"name": "TNT",         "domain": "tnt.fr",         "pattern": r"bonTransport=(\d+)"},
    {"name": "Colissimo",   "domain": "laposte.fr",     "pattern": r"code=(\d+)"},
    {"name": "Chronopost",  "domain": "chronopost.fr",  "pattern": r"skybillNumber=([A-Z0-9]+)"},
    {"name": "DB Schenker", "domain": "dbschenker.com", "pattern": r"refNumber=([A-Z0-9]+)"},
    {"name": "DPD",         "domain": "dpd.fr",         "pattern": r"parcelNumber=([A-Z0-9]+)"},
    {"name": "UPS",         "domain": "ups.com",        "pattern": r"tracknum=([A-Z0-9]+)"},
    {"name": "FedEx",       "domain": "fedex.com",      "pattern": r"tracknumbers=([A-Z0-9]+)"},
]


def _detect_carrier(tracking_url: str) -> tuple[str | None, str | None]:
    """Détecte le transporteur et extrait le numéro de suivi depuis l'URL."""
    if not tracking_url:
        return None, None
    for carrier in _CARRIERS:
        if carrier["domain"] in tracking_url.lower():
            m = re.search(carrier["pattern"], tracking_url)
            return carrier["name"], (m.group(1) if m else None)
    return "Inconnu", None


# ─── Classe moteur CSS ────────────────────────────────────────────────────────


class SetinTrackingScraper(BaseScraper):
    """Moteur CSS : extraction d'une ligne commande / helpers DOM + parsing."""

    SUPPLIER: str = "setin"
    DAYS: int = 7

    def __init__(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> None:
        super().__init__("setin_tracking")
        self._username: str = os.getenv("User_P5", "")
        self._password: str = os.getenv("Password_P5", "")
        now = datetime.now()
        if date_from is not None and date_to is not None:
            self._date_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
            self._date_to = date_to.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            self._date_from = now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=self.DAYS)
            self._date_to = now.replace(hour=23, minute=59, second=59, microsecond=0)
        self._max_pages = 500
        self._order_row_selector = Selectors.order_row
        if not self._username or not self._password:
            self.log.warning("User_P5 ou Password_P5 non défini dans .env")

    # ─── Connexion / session ──────────────────────────────────────────────────

    async def _is_logged_in(self, page: Page) -> bool:
        try:
            return await page.locator(Selectors.user_info).count() > 0
        except Exception:
            return False

    async def _connexion(self, page: Page) -> None:
        await page.locator(Selectors.account_icon).first.click(timeout=TIMEOUT_LONG)
        await page.get_by_placeholder(Selectors.email_placeholder).last.fill(self._username)
        await page.get_by_placeholder(Selectors.password_placeholder).last.fill(self._password)
        # Augmenter timeout pour le submit (serveur Setin lent)
        await page.locator(Selectors.submit).last.click(timeout=20000)
        # Attendre la navigation en parallèle avec timeout plus long
        try:
            await page.wait_for_navigation(timeout=20000)
        except Exception:
            self.log.debug("Wait_for_navigation timeout — continuant...")
        # Attendre les load states avec timeouts plus longs et ignorer les erreurs mineures
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            self.log.debug("Timeout domcontentloaded — continuant...")
        try:
            await page.wait_for_load_state("load", timeout=20000)
        except Exception:
            self.log.debug("Timeout load — continuant...")
        self.log.info("Connexion terminée — URL : %s", page.url)

    # ─── Extraction d'une commande (locator Playwright) ───────────────────────

    async def _extract_order(self, page: Page, order_el) -> dict | None:
        """Extrait les 10 champs tracking d'une ligne commande."""
        try:
            link_el = order_el.locator(Selectors.order_link)
            ref_px_raw = await link_el.inner_text()
            m = re.search(r"\bB\d+[A-Z]+\b", ref_px_raw)
            id_cmd = m.group(0) if m else ref_px_raw.strip()

            ref_cmd = ""
            try:
                ref_sel = Selectors.order_ref_template.format(ref_px=id_cmd)
                ref_cmd = (await order_el.locator(ref_sel).inner_text()).strip()
            except Exception:
                pass

            date_cmd = ""
            try:
                raw_date = await order_el.locator(Selectors.order_date).first.inner_text()
                date_cmd = self._normalize_date_label(raw_date)
            except Exception:
                pass

            statut_cmd = ""
            try:
                statut_cmd = (
                    await order_el.locator(Selectors.order_status).inner_text(timeout=1000)
                ).strip()
            except Exception:
                pass

            # Suivi transporteur depuis la liste
            carrier_exp: str | None = None
            trackinglink_exp: str | None = None
            tracking_exp: str | None = None

            tracking_loc = order_el.locator(Selectors.order_tracking)
            if await tracking_loc.count() > 0:
                tracking_url = await tracking_loc.first.get_attribute("href") or ""
                trackinglink_exp = tracking_url or None
                carrier_exp, tracking_exp = _detect_carrier(tracking_url)
                if carrier_exp == "Inconnu":
                    self.log.warning("Transporteur inconnu — %s : %s", id_cmd, tracking_url)

            # Date reliquat depuis la liste (ligne-4)
            Date_Reliquat: str | None = None
            try:
                reliquat_loc = order_el.locator(Selectors.order_reliquat)
                count = await reliquat_loc.count()
                if count > 0:
                    raw_rel = await reliquat_loc.first.inner_text()
                    normalized = self._normalize_date_label(raw_rel)
                    Date_Reliquat = normalized if normalized else None
                    if Date_Reliquat:
                        self.log.debug("[%s] Date reliquat trouvée : %s", id_cmd, Date_Reliquat)
                else:
                    self.log.debug("[%s] Sélecteur reliquat introuvable (chercher : %s)", id_cmd, Selectors.order_reliquat)
            except Exception as e:
                self.log.debug("[%s] Erreur extraction reliquat : %s", id_cmd, e)

            # data_pdt et weight_exp depuis la page de détail
            data_pdt = ""
            weight_exp: str | None = None
            try:
                product_href = await link_el.get_attribute("href") or ""
                if product_href:
                    product_url = f"{Selectors.BASE_URL}dhtml/{product_href}"
                    data_pdt, weight_exp = await self._extract_detail_page(page, product_url)
            except Exception as exc:
                log_exception(self.log, exc, f"detail page {id_cmd}")

            return {
                "id_cmd": id_cmd,
                "ref_cmd": ref_cmd,
                "date_cmd": date_cmd,
                "statut_cmd": statut_cmd,
                "data_pdt": data_pdt,
                "Date_Reliquat": Date_Reliquat,
                "weight_exp": weight_exp,
                "carrier_exp": carrier_exp,
                "trackinglink_exp": trackinglink_exp,
                "tracking_exp": tracking_exp,
            }

        except Exception as exc:
            log_exception(self.log, exc, "extract_order tracking")
            return None

    async def _extract_detail_page(self, page: Page, product_url: str) -> tuple[str, str | None]:
        """Ouvre la page détail et retourne (data_pdt, weight_exp).

        data_pdt : "ref:titre:qty" du premier article.
        weight_exp : poids de l'expédition si trouvé (sélecteur detail_weight).
        """
        new_page = None
        try:
            new_page = await page.context.new_page()
            await new_page.goto(product_url)
            await new_page.wait_for_load_state("domcontentloaded")

            # data_pdt
            data_pdt = ""
            try:
                articles = new_page.locator(Selectors.order_product_articles)
                title = (
                    await articles.locator(Selectors.order_product_label).first.inner_text()
                ).strip().replace(":", "-").replace(",", ".").replace(";", ".")
                ref = (
                    await articles.locator(Selectors.order_product_text).first.inner_text()
                ).strip()
                qty_raw = (
                    await articles.locator(Selectors.order_product_value).first.inner_text()
                ).strip()
                qty = int(float(qty_raw))
                data_pdt = f"{ref}:{title}:{qty}"
            except Exception:
                pass

            # weight_exp — sélecteur CSS d'abord, puis fallback regex sur le texte de la page
            weight_exp: str | None = None
            try:
                weight_loc = new_page.locator(Selectors.detail_weight)
                if await weight_loc.count() > 0:
                    weight_exp = (await weight_loc.first.inner_text()).strip() or None
                    self.log.debug("Poids expédition trouvé (CSS) : %s", weight_exp)
            except Exception as e:
                self.log.debug("Erreur sélecteur poids CSS : %s", e)

            if not weight_exp:
                try:
                    body_text = await new_page.locator("body").inner_text()
                    m_w = re.search(r"(\d+[.,]\d+)\s*kg", body_text, re.IGNORECASE)
                    if m_w:
                        weight_exp = m_w.group(0).strip()
                        self.log.debug("Poids expédition trouvé (regex) : %s", weight_exp)
                except Exception as e:
                    self.log.debug("Erreur fallback poids regex : %s", e)

            return data_pdt, weight_exp

        except Exception as exc:
            log_exception(self.log, exc, f"extract_detail_page {product_url}")
            return "", None
        finally:
            if new_page:
                try:
                    await new_page.close()
                except Exception:
                    pass

    # ─── Helpers — dates et pagination (identiques au scraper orders) ─────────

    @staticmethod
    def _normalize_date_label(raw: str) -> str:
        text = " ".join(raw.split())
        m = re.search(r"(\d{2}/\d{2}/\d{4})", text)
        return m.group(1) if m else text

    async def _get_order_date_str(self, order_el) -> str:
        try:
            raw = await order_el.locator(Selectors.order_date).first.inner_text()
            normalized = self._normalize_date_label(raw)
            return normalized if re.fullmatch(r"\d{2}/\d{2}/\d{4}", normalized) else raw.strip()
        except Exception:
            return "?"

    async def _parse_order_date(self, order_el) -> datetime | None:
        label = await self._get_order_date_str(order_el)
        try:
            return datetime.strptime(label, "%d/%m/%Y")
        except ValueError:
            return None

    async def _verify_order_row_selector(self, page: Page, selector: str) -> bool:
        try:
            row = page.locator(selector).first
            if await row.count() == 0:
                return False
            if await row.locator(Selectors.order_link).count() == 0:
                return False
            return True
        except Exception:
            return False

    async def _resolve_order_row_selector(self, page: Page) -> str | None:
        candidates = [
            Selectors.order_row,
            "div[class*='commande']:has(div.listing-setin-ligne-1)",
            "div[class*='commande']",
            "div[class*='row'] div.listing-setin-ligne-1",
        ]
        for selector in candidates:
            try:
                await page.wait_for_selector(selector, timeout=8000)
                if await self._verify_order_row_selector(page, selector):
                    self.log.debug("Sélecteur commande trouvé : %s", selector)
                    return selector
            except Exception:
                continue
        return None

    def _orders_pagination_locator(self, page: Page):
        root = Selectors.orders_pagination
        return page.locator(root).first

    async def _get_ui_page_number(self, page: Page) -> str | None:
        try:
            current = self._orders_pagination_locator(page).locator(
                Selectors.orders_pagination_current
            )
            if await current.count() == 0:
                return None
            return (await current.first.inner_text()).strip()
        except Exception:
            return None

    async def _first_order_id(self, page: Page) -> str:
        try:
            first_row = page.locator(self._order_row_selector).first
            link_el = first_row.locator(Selectors.order_link)
            ref_px_raw = await link_el.inner_text()
            m = re.search(r"\bB\d+[A-Z]+\b", ref_px_raw)
            return m.group(0) if m else ref_px_raw.strip()
        except Exception:
            return ""

    async def _has_next_orders_page(self, page: Page) -> bool:
        try:
            next_link = self._orders_pagination_locator(page).locator(
                Selectors.orders_pagination_next
            )
            return await next_link.count() > 0
        except Exception:
            return False

    async def _go_to_next_orders_page(self, page: Page, first_id_before: str) -> bool:
        try:
            next_link = self._orders_pagination_locator(page).locator(
                Selectors.orders_pagination_next
            ).first
            if await next_link.count() == 0:
                return False

            ui_before = await self._get_ui_page_number(page)
            await next_link.scroll_into_view_if_needed()
            await next_link.click()
            await page.wait_for_load_state("domcontentloaded")

            for _ in range(15):
                await page.wait_for_timeout(400)
                first_id_after = await self._first_order_id(page)
                ui_after = await self._get_ui_page_number(page)
                if first_id_before and first_id_after != first_id_before:
                    return True
                if ui_before and ui_after and ui_before != ui_after:
                    return True

            self.log.warning("Pagination : contenu inchangé après clic (ref=%s)", first_id_before or "?")
            return False
        except Exception as exc:
            self.log.error("Erreur pagination : %s", exc)
            return False
