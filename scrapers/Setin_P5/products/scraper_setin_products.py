"""Moteur CSS du scraper produits Setin.

Ce fichier contient uniquement les appels CSS : connexion, navigation catégories,
collecte des liens produits, extraction des variantes et helpers de parsing.
L'orchestration (run, persistance) se trouve dans scrap_setin_products.py.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from playwright.async_api import ElementHandle, Locator, Page, TimeoutError as PlaywrightTimeout

import selectors.setin as SEL
from core.base_scraper import BaseScraper
from core.config import TIMEOUT_LONG
from core.logger import log_exception
from core.utils import clean_text

load_dotenv()


class SetinProductScraper(BaseScraper):
    """Moteur CSS du catalogue produits Setin — sélecteurs, parsing, extraction HTML."""

    SUPPLIER: str = "setin"

    def __init__(self, category_name: str = "") -> None:
        """Initialise le scraper.

        Args:
            category_name: Nom exact de la catégorie à scraper (doit correspondre
                à une entrée de SEL.CATEGORY_NAMES). Si vide, prend la première.
        """
        super().__init__("setin_products")
        self._username: str = os.getenv("User_P5", "")
        self._password: str = os.getenv("Password_P5", "")
        self._category_name: str = category_name or SEL.CATEGORY_NAMES[0]
        if not self._username or not self._password:
            self.log.warning("User_P5 ou Password_P5 non défini dans .env")

    # ------------------------------------------------------------------
    # Connexion / session
    # ------------------------------------------------------------------

    async def _is_logged_in(self, page: Page) -> bool:
        """Renvoie True si l'utilisateur est déjà connecté (div.info-perso présent)."""
        try:
            return await page.locator(SEL.LOGIN["user_info"]).count() > 0
        except Exception:
            return False

    async def _connexion(self, page: Page) -> None:
        """Effectue la connexion au compte Setin."""
        self.log.debug("Connexion : clic icône compte (%s)", SEL.LOGIN["account_icon"])
        await page.locator(SEL.LOGIN["account_icon"]).first.click(timeout=TIMEOUT_LONG)

        self.log.debug("Connexion : remplissage email")
        await page.get_by_placeholder(SEL.LOGIN["email_placeholder"]).last.fill(
            self._username
        )

        self.log.debug("Connexion : remplissage mot de passe")
        await page.get_by_placeholder(SEL.LOGIN["password_placeholder"]).last.fill(
            self._password
        )

        self.log.debug("Connexion : clic submit (%s)", SEL.LOGIN["submit"])
        # Augmenter timeout pour le submit (serveur Setin lent)
        await page.locator(SEL.LOGIN["submit"]).last.click(timeout=20000)
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

    # ------------------------------------------------------------------
    # Navigation catégories
    # ------------------------------------------------------------------

    async def _get_categories(self, page: Page, category_name: str) -> list[str]:
        """Parcourt le menu à trois niveaux et collecte les URLs de sous-catégories.

        Args:
            page: Page Playwright active.
            category_name: Libellé exact du niveau 1 (aria-label).

        Returns:
            Liste des URLs de sous-catégories de niveau 3.
        """
        categories: list[str] = []
        await page.locator(SEL.NAVIGATION["menu_products"]).click()

        selector_lvl1 = SEL.NAVIGATION["category_level1"].format(
            category=category_name
        )
        catlvl1 = await page.locator(selector_lvl1).all()
        self.log.info("Catégories niveau 1 trouvées : %d", len(catlvl1))

        for catlv1 in catlvl1:
            try:
                await catlv1.click()
                await page.wait_for_timeout(400)
                catlv2s = await page.locator(SEL.NAVIGATION["category_level2"]).all()
                for catlvl2 in catlv2s:
                    try:
                        await catlvl2.click()
                        await page.wait_for_timeout(300)
                        catlvl3s = await page.locator(
                            SEL.NAVIGATION["category_level3"]
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

    # ------------------------------------------------------------------
    # Liste de produits d'une sous-catégorie
    # ------------------------------------------------------------------

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

        # Chargement progressif ("charger plus")
        try:
            while True:
                next_btn = page.locator(SEL.NAVIGATION["pagination_next"])
                try:
                    if await next_btn.count() == 0:
                        break
                    await next_btn.scroll_into_view_if_needed()
                    await next_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await page.wait_for_timeout(1000)
                    if await page.locator(SEL.NAVIGATION["pagination_next"]).count() == 0:
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
                SEL.NAVIGATION["product_box_link"]
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

    # ------------------------------------------------------------------
    # Extraction des données d'une page produit
    # ------------------------------------------------------------------

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
                SEL.PRODUCT_DETAIL["table_wait"], timeout=2000
            )
        except PlaywrightTimeout:
            pass

        table = page.locator(SEL.PRODUCT_DETAIL["product_table"])
        product_rows: list[Locator] = await table.locator(
            SEL.PRODUCT_DETAIL["product_row"]
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
                    await row.locator(SEL.PRODUCT_DETAIL["row_detail_button"]).click()
                    await page.wait_for_timeout(300)
                    # Déclencher le chargement du stock : SETIN l'affiche
                    # uniquement après sélection d'une variante + interaction quantité
                    try:
                        qty_loc = page.locator(SEL.PRODUCT_DETAIL["quantity_input"])
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

        # Toutes les images du produit (une par variante, dédupliquées)
        all_images = ", ".join(
            dict.fromkeys(p["image"] for p in products if p.get("image"))
        )
        for p in products:
            p["productImages"] = all_images

        return current_index, products

    # ------------------------------------------------------------------
    # Extraction d'une ligne variante
    # ------------------------------------------------------------------

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
                await row.locator(SEL.PRODUCT_DETAIL["row_title"]).inner_text()
            )
        except Exception as exc:
            log_exception(self.log, exc, f"{url} titre")

        # --- Référence ---
        ref = ""
        try:
            await page.wait_for_selector(SEL.PRODUCT_DETAIL["row_ref"], timeout=1000)
            ref = clean_text(
                await row.locator(SEL.PRODUCT_DETAIL["row_ref"]).inner_text()
            )
            if ref:
                grp_ref.append(ref)
        except Exception as exc:
            log_exception(self.log, exc, f"{url} ref")

        # --- Prix ---
        prix = ""
        try:
            raw = await page.locator(SEL.PRODUCT_DETAIL["unit_price"]).first.inner_text()
            prix = clean_text(raw.replace("€", ""))
        except Exception as exc:
            log_exception(self.log, exc, f"{url} prix")

        # --- Réduction (le prix barré devient le prix normal, l'actuel = promos) ---
        reduc: str | None = None
        try:
            strike_loc = page.locator(SEL.PRODUCT_DETAIL["strikethrough_price"])
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
            raw = await page.locator(SEL.PRODUCT_DETAIL["eco_tax"]).first.inner_text()
            eco_tax = clean_text(raw.replace("€", ""))
        except Exception as exc:
            log_exception(self.log, exc, f"{url} eco_tax")

        # --- Image variante ---
        image = ""
        try:
            image = await row.locator(SEL.PRODUCT_DETAIL["row_image"]).get_attribute(
                "src"
            ) or ""
        except Exception as exc:
            log_exception(self.log, exc, f"{url} image")

        # --- Statut stock ---
        # L'élément n'est visible qu'après sélection de la variante + trigger quantité
        stock_status = ""
        try:
            stock_loc = page.locator(SEL.PRODUCT_DETAIL["stock_status"])
            if await stock_loc.count() == 0:
                stock_loc = row.locator(SEL.PRODUCT_DETAIL["stock_status"])
            try:
                await stock_loc.first.wait_for(state="visible", timeout=3000)
            except PlaywrightTimeout:
                pass
            texts = await stock_loc.all_inner_texts()
            if not any(t.strip() for t in texts):
                alt_loc = row.locator("div.mise_en_avant_stock, span.texte_stock, span.texte")
                if await alt_loc.count() > 0:
                    texts = await alt_loc.all_inner_texts()
            stock_status = ", ".join(t.strip() for t in texts if t.strip())
        except Exception as exc:
            log_exception(self.log, exc, f"{url} stock_status")

        # --- Marque ---
        marque, img_marque = "", ""
        try:
            brand_el = page.locator(SEL.PRODUCT_DETAIL["brand_image"])
            marque = await brand_el.get_attribute("title") or ""
            img_marque = await brand_el.get_attribute("src") or ""
        except Exception as exc:
            log_exception(self.log, exc, f"{url} marque")

        # --- Documents ---
        doc_list: list[str] = []
        try:
            doc_handles: list[ElementHandle] = await page.locator(
                SEL.PRODUCT_DETAIL["doc_links"]
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
            detail_sel = SEL.PRODUCT_DETAIL["detail_panel_template"].format(
                row_id=row_id
            )
            detail = page.locator(detail_sel)

            ean = clean_text(
                await detail.locator(SEL.PRODUCT_DETAIL["ean_value"]).inner_text()
            )
            four = clean_text(
                await detail.locator(SEL.PRODUCT_DETAIL["supplier_ref"]).inner_text()
            )

            # Description courte
            try:
                if (
                    await detail.locator(
                        SEL.PRODUCT_DETAIL["short_desc_article"]
                    ).count()
                    > 0
                ):
                    desc = clean_text(
                        await page.locator(
                            SEL.PRODUCT_DETAIL["short_desc_article"]
                        ).inner_text()
                    )
                else:
                    desc = clean_text(
                        await page.locator(
                            SEL.PRODUCT_DETAIL["short_desc_variant"]
                        ).inner_text()
                    )
            except Exception as exc:
                log_exception(self.log, exc, f"{url} description")

            # Caractéristiques de la description longue
            try:
                long_sel = SEL.PRODUCT_DETAIL["long_description"]
                if await page.locator(long_sel).count() > 0:
                    carac_sel = (
                        f"{long_sel} {SEL.PRODUCT_DETAIL['characteristic_row']}"
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
                var_carac_sel = SEL.PRODUCT_DETAIL["variation_characteristics"]
                if (
                    nb_rows > 1
                    and await detail.locator(var_carac_sel).count() > 0
                ):
                    all_refs = await page.locator(
                        SEL.PRODUCT_DETAIL["row_ref"]
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
        }

        self.log.info("Produit extrait : %s", ref)
        return produit, update_current_index

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ariane(self, page: Page) -> tuple[str, str, str]:
        """Extrait les trois premiers niveaux du fil d'Ariane."""
        try:
            handles: list[ElementHandle] = await page.locator(
                SEL.NAVIGATION["breadcrumb"]
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
        for sel in SEL.PRODUCT_DETAIL["packaging_selectors"]:
            loc = row.locator(sel)
            if await loc.count() > 0:
                return clean_text(await loc.inner_text())
        return ""
