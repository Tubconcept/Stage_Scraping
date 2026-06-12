"""
Moteur CSS du scraper produits Setin (site P5).

Rôle :
    Extraction pure depuis le DOM Setin : connexion, navigation dans le menu
    à trois niveaux, pagination « charger plus », parsing des fiches produit
    et de leurs variantes (tableau de déclinaisons).

Type : produits.

Architecture :
    - scraper_setin_products.py (ce fichier) = couche CSS / parsing HTML.
    - scrap_setin_products.py = orchestrateur (run, SQLite, reprise, CLI).
    Aucune écriture en base ici : les données sont renvoyées via to_csv_row().

Sélecteurs centralisés dans selectors/setin.py (classe Selectors).
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os
from urllib.parse import quote

from dotenv import load_dotenv
from playwright.async_api import ElementHandle, Locator, Page, TimeoutError as PlaywrightTimeout

from css_selectors.setin import Selectors
from core.base_scraper import BaseScraper
from core.config import CSV_HEADERS, TIMEOUT_LONG
from core.logger import log_exception
from core.utils import clean_text

load_dotenv()

# ─── Normalisation du statut stock ────────────────────────────────────────────
# Setin DOM returns raw labels ("En stock", "Disponible", "Hors stock", …).
# Only two values may be stored in the DB.
_EN_STOCK_TOKENS = frozenset(("en stock", "disponible", "dispo"))


def _normalize_stock_status(raw: str) -> str:
    """Map any Setin stock label to 'EN STOCK' or 'PAS EN STOCK'."""
    lowered = (raw or "").lower().strip()
    if lowered and any(tok in lowered for tok in _EN_STOCK_TOKENS):
        return "EN STOCK"
    return "PAS EN STOCK"


# ─── Classe moteur CSS ────────────────────────────────────────────────────────

class SetinProductScraper(BaseScraper):
    """Moteur CSS du catalogue produits Setin — sélecteurs, parsing, extraction HTML."""

    SUPPLIER: str = "setin"

    def __init__(self, category_name: str = "") -> None:
        """Initialise le scraper.

        Args:
            category_name: Nom exact de la catégorie à scraper (doit correspondre
                à une entrée de Selectors.CATEGORY_NAMES). Si vide, prend la première.
        """
        super().__init__("setin_products")
        self._username: str = os.getenv("User_P5", "")
        self._password: str = os.getenv("Password_P5", "")
        self._category_name: str = category_name or Selectors.CATEGORY_NAMES[0]
        self._first_page_only: bool = False
        if not self._username or not self._password:
            self.log.warning("User_P5 ou Password_P5 non défini dans .env")

    # ─── Connexion / session ──────────────────────────────────────────────────

    async def _is_logged_in(self, page: Page) -> bool:
        """Renvoie True si l'utilisateur est déjà connecté (div.info-perso présent)."""
        try:
            return await page.locator(Selectors.user_info).count() > 0
        except Exception:
            return False

    async def _connexion(self, page: Page) -> None:
        """Effectue la connexion au compte Setin."""
        self.log.debug("Connexion : clic icône compte (%s)", Selectors.account_icon)
        await page.locator(Selectors.account_icon).first.click(timeout=TIMEOUT_LONG)

        self.log.debug("Connexion : remplissage email")
        await page.get_by_placeholder(Selectors.email_placeholder).last.fill(
            self._username
        )

        self.log.debug("Connexion : remplissage mot de passe")
        await page.get_by_placeholder(Selectors.password_placeholder).last.fill(
            self._password
        )

        self.log.debug("Connexion : clic submit (%s)", Selectors.submit)
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

    # ─── Navigation catégories ────────────────────────────────────────────────

    async def _get_categories(self, page: Page, category_name: str) -> list[str]:
        """Parcourt le menu à trois niveaux et collecte les URLs de sous-catégories.

        Args:
            page: Page Playwright active.
            category_name: Libellé exact du niveau 1 (aria-label).

        Returns:
            Liste des URLs de sous-catégories de niveau 3.
        """
        categories: list[str] = []
        await page.locator(Selectors.menu_products).click()

        selector_lvl1 = Selectors.category_level1.format(
            category=category_name
        )
        catlvl1 = await page.locator(selector_lvl1).all()
        self.log.info("Catégories niveau 1 trouvées : %d", len(catlvl1))

        for catlv1 in catlvl1:
            try:
                await catlv1.click()
                await page.wait_for_timeout(400)
                catlv2s = await page.locator(Selectors.category_level2).all()
                for catlvl2 in catlv2s:
                    try:
                        await catlvl2.click()
                        await page.wait_for_timeout(300)
                        catlvl3s = await page.locator(
                            Selectors.category_level3
                        ).all()
                        for link_el in catlvl3s:
                            try:
                                href = await link_el.get_attribute("href")
                                if href:
                                    categories.append(href)
                                    self.log.debug("Lien catégorie : %s", href)
                            except Exception as exc:
                                log_exception(self.log, exc, "Récup lien lvl3")
                    except Exception as exc:
                        log_exception(self.log, exc, "Récup cat lvl3")
            except Exception as exc:
                log_exception(self.log, exc, "Récup cat lvl2")

        return categories

    # ─── Liste de produits d'une sous-catégorie ───────────────────────────────

    async def _get_products_link(
        self, page: Page, url: str
    ) -> tuple[list[str], str, str, str]:
        """Visite une sous-catégorie, charge tous les produits et renvoie leurs liens.

        Args:
            page: Page Playwright active.
            url: URL de la sous-catégorie.

        Returns:
            Tuple (liste d'URLs produit, cat1, cat2, cat3).
        """
        products: list[str] = []
        self.log.info("Visite sous-catégorie : %s", url)
        await page.goto(url)
        await page.wait_for_load_state("domcontentloaded")

        # Détection de redirection vers un produit unique
        current_url = page.url
        if current_url != url:
            self.log.info("Redirection détectée → produit unique : %s", current_url)
            products.append(current_url)
            cat1, cat2, cat3 = await self._ariane(page)
            return products, cat1, cat2, cat3

        # Chargement progressif ("charger plus") — ignoré si _first_page_only=True
        if not self._first_page_only:
            try:
                while True:
                    next_btn = page.locator(Selectors.pagination_next)
                    try:
                        if await next_btn.count() == 0:
                            break
                        await next_btn.scroll_into_view_if_needed()
                        await next_btn.click()
                        await page.wait_for_load_state("domcontentloaded")
                        await page.wait_for_timeout(1000)
                        if await page.locator(Selectors.pagination_next).count() == 0:
                            break
                    except PlaywrightTimeout:
                        break
                    except Exception:
                        break
            except Exception as exc:
                log_exception(self.log, exc, f"Pagination {url}")

        try:
            await page.wait_for_selector("div.product_box", timeout=5000)
            box_handles: list[ElementHandle] = await page.locator(
                Selectors.product_box_link
            ).element_handles()
            for box in box_handles:
                try:
                    link = await box.get_attribute("href")
                    if link:
                        products.append(link)
                except Exception as exc:
                    log_exception(self.log, exc, "Obtention lien produit")
            cat1, cat2, cat3 = await self._ariane(page)
        except Exception as exc:
            cat1, cat2, cat3 = "fail", "fail", "fail"
            log_exception(self.log, exc, f"Liens produits / ariane {url}")

        self.log.info("%d liens produits collectés", len(products))
        return products, cat1, cat2, cat3

    # ─── Extraction des données d'une page produit ────────────────────────────

    async def _get_product_data(
        self, page: Page, url: str, current_index: int
    ) -> tuple[int, list[dict]]:
        """Visite une page produit et extrait toutes ses variantes.

        Args:
            page: Page Playwright active.
            url: URL de la fiche produit.
            current_index: Index courant de combinaison (pour le lien parent).

        Returns:
            Tuple (index mis à jour, liste de dicts produit).
        """
        self.log.info("Visite produit : %s", url)
        await page.goto(url)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_selector(
                Selectors.table_wait, timeout=2000
            )
        except PlaywrightTimeout:
            pass

        table = page.locator(Selectors.product_table)
        product_rows: list[Locator] = await table.locator(
            Selectors.product_row
        ).all()
        nb_rows = len(product_rows)
        self.log.debug("%d ligne(s) produit sur %s", nb_rows, url)

        grp_ref: list[str] = []
        update_index = False
        products: list[dict] = []

        for row in product_rows:
            classes = await row.get_attribute("class") or ""
            if "ligne_ouverte" not in classes:
                try:
                    await row.locator(Selectors.row_detail_button).click()
                    await page.wait_for_timeout(300)
                    # Déclencher le chargement du stock : SETIN l'affiche
                    # uniquement après sélection d'une variante + interaction quantité
                    try:
                        qty_loc = page.locator(Selectors.quantity_input)
                        if await qty_loc.count() > 0:
                            await qty_loc.first.fill("1")
                            await qty_loc.first.press("Tab")
                            await page.wait_for_timeout(500)
                    except Exception:
                        pass
                except Exception as exc:
                    log_exception(self.log, exc, f"Click détail {url}")

            try:
                produit, row_updates_index = await self._extract_row_data(
                    page, row, url, grp_ref, nb_rows, current_index
                )
                products.append(produit)
                if row_updates_index:
                    update_index = True
            except Exception as exc:
                log_exception(self.log, exc, f"Extraction ligne {url}")

        if update_index:
            current_index += 1

        # Champs communs à toutes les variantes (page produit)
        page_status = await self._get_product_status(page)
        eco_labels = await self._get_eco_labels(page)

        all_images = "||".join(
            dict.fromkeys(p["image"] for p in products if p.get("image"))
        )

        # Toutes les références du groupe : parent || enfant1 || enfant2 || ...
        all_group_refs = list(dict.fromkeys(p["ref"] for p in products if p.get("ref")))
        group_refs_set = set(all_group_refs)
        group_refs_str = "||".join(all_group_refs)
        parent_ref = all_group_refs[0] if all_group_refs else ""

        for p in products:
            p["productImages"]  = all_images
            p["product_status"] = page_status
            p["product_eco_label"] = eco_labels
            p["group_refs"]     = group_refs_str
            if not p.get("parent"):
                p["parent"]     = parent_ref
            # Filtre : exclure les refs du groupe lui-même du cross-sell
            cs_raw = p.get("product_cross_sell", "")
            if cs_raw and group_refs_set:
                filtered = [r for r in cs_raw.split("||") if r and r not in group_refs_set]
                p["product_cross_sell"] = "||".join(filtered)
            elif not cs_raw:
                p["product_cross_sell"] = ""

        return current_index, products

    # ─── Extraction d'une ligne variante ──────────────────────────────────────

    async def _extract_row_data(
        self,
        page: Page,
        row: Locator,
        url: str,
        grp_ref: list[str],
        nb_rows: int,
        current_index: int,
    ) -> tuple[dict, bool]:
        """Extrait toutes les données d'une ligne variante (tableau de variations).

        Args:
            page: Page Playwright active.
            row: Locator de la ligne variante.
            url: URL de la fiche produit (pour les logs).
            grp_ref: Liste des références du groupe (modifiée en place).
            nb_rows: Nombre total de variantes sur la page.
            current_index: Index courant de combinaison.

        Returns:
            Tuple (dict produit, booléen indiquant si l'index doit être incrémenté).
        """
        # --- Titre ---
        title = ""
        try:
            title = clean_text(
                await row.locator(Selectors.product_designation).inner_text()
            )
        except Exception as exc:
            log_exception(self.log, exc, f"{url} titre")

        # --- Référence ---
        ref = ""
        try:
            await page.wait_for_selector(Selectors.product_reference_fournisseur, timeout=1000)
            ref = clean_text(
                await row.locator(Selectors.product_reference_fournisseur).inner_text()
            )
            if ref:
                grp_ref.append(ref)
        except Exception as exc:
            log_exception(self.log, exc, f"{url} ref")

        # --- Prix ---
        prix = ""
        try:
            raw = await page.locator(Selectors.product_price_ht).first.inner_text()
            prix = clean_text(raw.replace("€", ""))
        except Exception as exc:
            log_exception(self.log, exc, f"{url} prix")

        # --- Réduction (le prix barré devient le prix normal, l'actuel = promos) ---
        reduc: str | None = None
        try:
            strike_loc = page.locator(Selectors.product_promotion)
            if await strike_loc.count() > 0:
                reduc = prix
                prix = clean_text(
                    (await strike_loc.first.inner_text()).replace("€", "")
                )
        except Exception as exc:
            log_exception(self.log, exc, f"{url} réduction")

        # --- Éco-taxe ---
        eco_tax = ""
        try:
            raw = await page.locator(Selectors.product_eco_taxe).first.inner_text()
            eco_tax = clean_text(raw.replace("€", ""))
        except Exception as exc:
            log_exception(self.log, exc, f"{url} eco_tax")

        # --- Image variante ---
        image = ""
        try:
            image = await row.locator(Selectors.product_image_url).get_attribute(
                "src"
            ) or ""
        except Exception as exc:
            log_exception(self.log, exc, f"{url} image")

        # --- Statut stock ---
        # L'élément n'est visible qu'après sélection de la variante + trigger quantité
        stock_status = ""
        try:
            stock_loc = page.locator(Selectors.product_stock_status)
            if await stock_loc.count() == 0:
                stock_loc = row.locator(Selectors.product_stock_status)
            try:
                await stock_loc.first.wait_for(state="visible", timeout=3000)
            except PlaywrightTimeout:
                pass
            texts = await stock_loc.all_inner_texts()
            if not any(t.strip() for t in texts):
                alt_loc = row.locator(Selectors.product_stock_status_alt)
                if await alt_loc.count() > 0:
                    texts = await alt_loc.all_inner_texts()
            stock_status = ", ".join(t.strip() for t in texts if t.strip())
        except Exception as exc:
            log_exception(self.log, exc, f"{url} stock_status")

        # --- Marque ---
        marque, img_marque = "", ""
        try:
            brand_el = page.locator(Selectors.product_brand)
            marque = await brand_el.get_attribute("title") or ""
            img_marque = await brand_el.get_attribute("src") or ""
        except Exception as exc:
            log_exception(self.log, exc, f"{url} marque")

        # --- Documents ---
        doc_list: list[str] = []
        try:
            doc_handles: list[ElementHandle] = await page.locator(
                Selectors.product_docs_url
            ).element_handles()
            for doc in doc_handles:
                href = await doc.get_attribute("href")
                if href:
                    doc_list.append(href)
        except Exception as exc:
            log_exception(self.log, exc, f"{url} docs")

        # --- Conditionnement ---
        cdt = await self._get_conditionnement(row)

        # --- Panneau de détail (EAN, ref fournisseur, description, caractéristiques) ---
        caracteristiques: dict[str, str] = {}
        desc = ""
        ean = ""
        four = ""
        decli_value: list[str] = []
        is_combination = False
        index_combination: int | None = None
        update_current_index = False
        ref_lier = ""

        try:
            row_id = await row.get_attribute("data-id")
            detail_sel = Selectors.detail_panel_template.format(
                row_id=row_id
            )
            detail = page.locator(detail_sel)

            ean = clean_text(
                await detail.locator(Selectors.product_ean).inner_text()
            )
            four = clean_text(
                await detail.locator(Selectors.product_reference_fabricant).inner_text()
            )

            # Description courte
            try:
                if (
                    await detail.locator(
                        Selectors.product_description_article
                    ).count()
                    > 0
                ):
                    desc = clean_text(
                        await page.locator(
                            Selectors.product_description_article
                        ).inner_text()
                    )
                else:
                    desc = clean_text(
                        await page.locator(
                            Selectors.product_description_variant
                        ).inner_text()
                    )
            except Exception as exc:
                log_exception(self.log, exc, f"{url} description")

            # Caractéristiques de la description longue
            try:
                long_sel = Selectors.product_attributes_block
                if await page.locator(long_sel).count() > 0:
                    carac_sel = (
                        f"{long_sel} {Selectors.product_attributes_row}"
                    )
                    for car in await page.locator(carac_sel).all():
                        b_el = car.locator("b")
                        span_el = car.locator("span")
                        if await b_el.count() > 0 and await span_el.count() > 0:
                            nom = clean_text(await b_el.inner_text())
                            val = clean_text(await span_el.inner_text())
                            caracteristiques[nom] = val
            except Exception as exc:
                log_exception(self.log, exc, f"{url} caractéristiques")

            # Déclinaisons / combinaisons
            try:
                var_carac_sel = Selectors.product_combination_values
                if (
                    nb_rows > 1
                    and await detail.locator(var_carac_sel).count() > 0
                ):
                    all_refs = await page.locator(
                        Selectors.product_reference_fournisseur
                    ).all_inner_texts()
                    # La deuxième moitié correspond aux refs visibles (hors doublons DOM)
                    group_ref = all_refs[len(all_refs) // 2 :]
                    if ref in group_ref:
                        group_ref.remove(ref)

                    index_combination = current_index
                    update_current_index = True
                    is_combination = True

                    carac_divs = detail.locator(var_carac_sel)
                    for i in range(await carac_divs.count()):
                        div = carac_divs.nth(i)
                        if (
                            await div.locator("b").count() == 1
                            and await div.locator("span").count() == 1
                        ):
                            key = clean_text(await div.locator("b").inner_text())
                            val = clean_text(await div.locator("span").inner_text())
                            decli_value.append(f"{key}:{val}")

                    ref_lier = "|".join(group_ref)
            except Exception as exc:
                log_exception(self.log, exc, f"{url} déclinaisons")

        except Exception as exc:
            log_exception(self.log, exc, f"{url} panneau détail")

        # Cross-sell pour CE variant spécifique (état de la page au moment de la sélection)
        cross_sell_row = await self._get_cross_sell(page)

        produit = {
            "title": title,
            "cdt": cdt,
            "ean": ean,
            "four": four,
            "marque": marque,
            "image": image,
            "ref": ref,
            "description": desc,
            "doc": ",".join(doc_list),
            "IndexCombination": index_combination,
            "IsCombination": is_combination,
            "combinationValues": "||".join(decli_value),
            "caractéristiques": "||".join(
                f"{k}:{v}" for k, v in caracteristiques.items()
            ),
            "parent": grp_ref[0] if grp_ref else "",
            "prix": prix,
            "eco_tax": eco_tax,
            "imgMarque": img_marque,
            "Produit lié": list(grp_ref),
            "reduc": reduc,
            "ref_decli": ref_lier,
            "stockStatus": stock_status,
            "product_cross_sell": cross_sell_row,
        }

        self.log.info("Produit extrait : %s", ref)
        return produit, update_current_index

    # ─── Helpers (fil d'Ariane, conditionnement, cross-sell, mode commandes) ─

    async def _ariane(self, page: Page) -> tuple[str, str, str]:
        """Extrait les trois premiers niveaux du fil d'Ariane."""
        try:
            handles: list[ElementHandle] = await page.locator(
                Selectors.breadcrumb
            ).element_handles()
            cat1 = clean_text(await handles[0].inner_text())
            cat2 = clean_text(await handles[1].inner_text())
            cat3 = clean_text(await handles[2].inner_text())
        except Exception as exc:
            cat1, cat2, cat3 = "fail", "fail", "fail"
            log_exception(self.log, exc, "Ariane")
        return cat1, cat2, cat3

    async def _get_conditionnement(self, row: Locator) -> str:
        """Extrait le conditionnement depuis les sélecteurs prioritaires."""
        for sel in Selectors.product_conditionnement:
            loc = row.locator(sel)
            if await loc.count() > 0:
                return clean_text(await loc.inner_text())
        return ""

    async def _get_product_status(self, page: Page) -> str:
        """Statut fin de vie / discontinué (page produit)."""
        try:
            loc = page.locator(Selectors.product_status)
            if await loc.count() > 0:
                return clean_text(await loc.first.inner_text())
        except Exception as exc:
            log_exception(self.log, exc, "product_status")
        return ""

    async def _get_cross_sell(self, page: Page) -> str:
        """Références des produits associés — extraites depuis l'href (ex: /BL6122 → BL6122)."""
        refs: list[str] = []
        try:
            links = page.locator(Selectors.product_cross_sell)
            for i in range(await links.count()):
                href = (await links.nth(i).get_attribute("href") or "").rstrip("/")
                token = href.split("/")[-1] if "/" in href else href
                if not token:
                    token = clean_text(await links.nth(i).inner_text())
                if token and token not in refs:
                    refs.append(token)
        except Exception as exc:
            log_exception(self.log, exc, "product_cross_sell")
        return "||".join(refs)

    async def _get_eco_labels(self, page: Page) -> str:
        """Labels éco-certification (alt/title des images)."""
        labels: list[str] = []
        try:
            imgs = page.locator(Selectors.product_eco_label)
            for i in range(await imgs.count()):
                alt = await imgs.nth(i).get_attribute("alt") or ""
                title = await imgs.nth(i).get_attribute("title") or ""
                label = clean_text(alt or title)
                if label and label not in labels:
                    labels.append(label)
        except Exception as exc:
            log_exception(self.log, exc, "product_eco_label")
        return "||".join(labels)

    async def _resolve_product_page_url(self, page: Page, url: str) -> str | None:
        """Résout une URL de recherche ou fiche vers la page produit catalogue."""
        try:
            await page.goto(url)
            await page.wait_for_load_state("domcontentloaded")
            if await page.locator(Selectors.table_wait).count() > 0:
                return page.url
            links = page.locator(Selectors.product_box_link)
            if await links.count() > 0:
                href = await links.first.get_attribute("href")
                if href:
                    return href if href.startswith("http") else f"{Selectors.BASE_URL.rstrip('/')}/{href.lstrip('/')}"
        except Exception as exc:
            log_exception(self.log, exc, f"resolve product url {url}")
        return None

    async def _collect_product_urls_from_orders(self, page: Page) -> list[str]:
        """Collecte les URLs produit via les commandes dans la plage de dates."""
        if not hasattr(self, "_date_from") or not hasattr(self, "_date_to"):
            self.log.error("Plage de dates non configurée pour le mode commandes")
            return []

        product_urls: set[str] = set()
        await page.goto(Selectors.ORDERS_URL)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.locator(Selectors.page_loader).wait_for(state="hidden", timeout=10000)
        except Exception:
            pass

        self._order_row_selector = await self._resolve_order_row_selector(page)  # type: ignore[attr-defined]
        if not self._order_row_selector:
            self.log.warning("Impossible de trouver les lignes commande")
            return []

        current_page = 1
        while current_page <= self._max_pages:  # type: ignore[attr-defined]
            order_elements = await page.locator(self._order_row_selector).all()
            if not order_elements:
                break

            stop_by_date = False
            for order_el in order_elements:
                order_date = await self._parse_order_date(order_el)  # type: ignore[attr-defined]
                if order_date is None:
                    continue
                if order_date > self._date_to:
                    continue
                if order_date < self._date_from:
                    stop_by_date = True
                    break

                link_el = order_el.locator(Selectors.order_link)
                href = await link_el.get_attribute("href") or ""
                if not href:
                    continue
                detail_url = f"{Selectors.BASE_URL}dhtml/{href.lstrip('/')}"
                new_page = None
                try:
                    new_page = await page.context.new_page()
                    await new_page.goto(detail_url)
                    await new_page.wait_for_load_state("domcontentloaded")
                    articles = new_page.locator(Selectors.order_product_articles)
                    n = await articles.count()
                    for i in range(n):
                        block = articles.nth(i)
                        ref = clean_text(
                            await block.locator(Selectors.order_product_text).inner_text()
                        )
                        prod_link = block.locator(
                            "a[href*='fiche'], a[href*='article'], a[href*='produit']"
                        )
                        if await prod_link.count() > 0:
                            h = await prod_link.first.get_attribute("href") or ""
                            if h:
                                full = (
                                    h
                                    if h.startswith("http")
                                    else f"{Selectors.BASE_URL.rstrip('/')}/{h.lstrip('/')}"
                                )
                                product_urls.add(full)
                        elif ref:
                            product_urls.add(
                                f"{Selectors.BASE_URL}recherche/?recherche={quote(ref)}"
                            )
                except Exception as exc:
                    log_exception(self.log, exc, f"détail commande {detail_url}")
                finally:
                    if new_page:
                        try:
                            await new_page.close()
                        except Exception:
                            pass

            if stop_by_date:
                break
            first_id = await self._first_order_id(page)  # type: ignore[attr-defined]
            if not await self._has_next_orders_page(page):  # type: ignore[attr-defined]
                break
            if not await self._go_to_next_orders_page(page, first_id):  # type: ignore[attr-defined]
                break
            current_page += 1

        resolved: list[str] = []
        for raw_url in product_urls:
            if "recherche" in raw_url.lower():
                final = await self._resolve_product_page_url(page, raw_url)
                if final:
                    resolved.append(final)
            else:
                resolved.append(raw_url)

        self.log.info(
            "%d URL(s) produit collectée(s) via commandes (%s → %s)",
            len(resolved),
            self._date_from.strftime("%d/%m/%Y"),
            self._date_to.strftime("%d/%m/%Y"),
        )
        return list(dict.fromkeys(resolved))

    @staticmethod
    def to_csv_row(
        produit: dict,
        cat1: str,
        cat2: str,
        cat3: str,
        source_url: str,
    ) -> dict[str, str]:
        """Mappe le dict interne vers les colonnes CSV_HEADERS."""
        category_tree = "||".join(
            c for c in (cat1, cat2, cat3) if c and c != "fail"
        )
        images = produit.get("productImages") or produit.get("image", "")
        is_combo = produit.get("IsCombination", False)
        return {
            "product_fournisseur": "P5",
            "product_reference_fournisseur": produit.get("ref", ""),
            "product_ean": produit.get("ean", ""),
            "product_reference_fabricant": produit.get("four", ""),
            "product_brand": produit.get("marque", ""),
            "product_brand_logo_url": produit.get("imgMarque", ""),
            "product_designation": produit.get("title", ""),
            "product_description": produit.get("description", ""),
            "product_image_url": images,
            "product_docs_url": produit.get("doc", "").replace(",", "||"),
            "product_category_tree": category_tree,
            "product_conditionnement": produit.get("cdt", ""),
            "product_stock_status": _normalize_stock_status(produit.get("stockStatus", "")),
            "product_status": produit.get("product_status", ""),
            "product_fournisseur_url": source_url,
            "product_eco_label": produit.get("product_eco_label", ""),
            "product_eco_taxe": produit.get("eco_tax", ""),
            "product_promotion": produit.get("reduc") or "",
            "product_price_ht": produit.get("prix", ""),
            "product_attributes": produit.get("caractéristiques", ""),
            "products_is_combination": str(is_combo),
            "product_combination_index": str(produit.get("IndexCombination") or ""),
            "product_parent_reference": produit.get("parent", "") or produit.get("ref", ""),
            "product_child_reference": produit.get("group_refs", "") or produit.get("ref", ""),
            "product_combination_values": produit.get("combinationValues", ""),
            "product_cross_sell": produit.get("product_cross_sell", ""),
        }
