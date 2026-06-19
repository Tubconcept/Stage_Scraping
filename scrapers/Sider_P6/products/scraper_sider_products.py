"""
Moteur CSS du scraper produits Sider (site P6).

Rôle :
    Extraction pure depuis le DOM Sider : connexion, découverte dynamique du menu,
    pagination, parsing des fiches produit et de leurs déclinaisons.

Type : produits.

Architecture :
    - scraper_sider_products.py (ce fichier) = couche CSS / parsing HTML.
    - scrap_sider_products.py = orchestrateur (run, MariaDB, reprise, CLI).
    Aucune écriture en base ici : les données sont renvoyées via to_csv_row().

Sélecteurs centralisés dans css_selectors/sider.py (classe Selectors).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from css_selectors.sider import Selectors
from core.base_scraper import BaseScraper
from core.logger import log_exception
from core.utils import clean_text

load_dotenv()


# ─── Helpers prix ─────────────────────────────────────────────────────────────

def _clean_price(raw: str) -> str:
    """Normalise un prix brut : retire €, &nbsp;, remplace virgule par point."""
    return raw.replace("\xa0", " ").replace("€", "").replace(",", ".").strip()


def _clean_eco_taxe(raw: str) -> str:
    """Normalise une éco-taxe : retourne '' si valeur absente ('-' ou vide)."""
    cleaned = raw.replace("\xa0", "").replace("€", "").replace(",", ".").strip()
    return "" if cleaned in ("-", "") else cleaned


# ─── Moteur CSS ───────────────────────────────────────────────────────────────

class SiderProductScraper(BaseScraper):
    """Moteur CSS du catalogue produits Sider — sélecteurs, parsing, extraction HTML."""

    SUPPLIER: str = "sider"

    def __init__(self) -> None:
        super().__init__("sider_products")
        self._username: str = os.getenv("User_P6", "")
        self._password: str = os.getenv("Password_P6", "")
        if not self._username or not self._password:
            self.log.warning("User_P6 ou Password_P6 non défini dans .env")

    # ─── Cookies / connexion ─────────────────────────────────────────────────

    async def _accept_cookies(self, page: Page) -> None:
        """Accepte la bannière de consentement si elle est présente."""
        try:
            btn = page.locator(Selectors.cookie_accept)
            if await btn.count() > 0:
                await btn.click()
                await page.wait_for_timeout(500)
        except Exception as exc:
            log_exception(self.log, exc, "cookie_accept")

    async def _connexion(self, page: Page) -> None:
        """Connexion au compte Sider. Ignorée si la session est déjà active.

        Détection : si account_icon est absent, la session est valide (bouton
        "Création de compte/Connexion" n'apparaît que pour un visiteur non connecté).
        Sinon, on clique, puis on vérifie la présence de #_email pour confirmer.
        """
        icon = page.locator(Selectors.account_icon)
        if await icon.count() == 0:
            self.log.info("Session déjà active — icône connexion absente")
            return

        await icon.click()
        await page.wait_for_load_state("domcontentloaded")

        if await page.locator(Selectors.email_input).count() == 0:
            self.log.info("Session déjà active — pas de reconnexion")
            return

        await page.locator(Selectors.email_input).fill(self._username)
        await page.locator(Selectors.password_input).fill(self._password)
        await page.locator(Selectors.submit).click()
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(2000)
        self.log.info("Connexion terminée — URL : %s", page.url)

    # ─── Navigation catalogue (découverte dynamique) ──────────────────────────

    async def _get_categories(
        self, page: Page, category_name: str | None = None
    ) -> list[str]:
        """Découverte dynamique du menu : hover niveau 1, collecte des URLs niveau 2.

        category_name : texte exact d'un élément du megamenu (ex: "OUTILLAGE").
            None ou "Toutes les catégories" → tout le catalogue est parcouru.
            Correspondance insensible à la casse après strip.

        L'index 0 (= "TOUT LE CATALOGUE") est toujours ignoré.

        Returns:
            Liste d'URLs complètes des sous-catégories.
        """
        categories: list[str] = []

        await page.locator(Selectors.all_categories_link).click()
        await page.wait_for_load_state("domcontentloaded")

        tier1 = await page.locator(Selectors.menu_level1).all()
        self.log.info("Entrées menu niveau 1 : %d", len(tier1))

        filtre = (
            None
            if not category_name or category_name.lower() == "toutes les catégories"
            else category_name.strip().lower()
        )

        for lien in tier1[1:]:
            try:
                if filtre:
                    texte = clean_text(await lien.inner_text()).strip().lower()
                    if texte != filtre:
                        continue
                await lien.hover()
                await page.wait_for_timeout(500)
                parent_li = lien.locator("xpath=..")
                for sc in await parent_li.locator(Selectors.menu_level2).all():
                    href = await sc.get_attribute("href") or ""
                    if href:
                        full = (
                            href if href.startswith("http")
                            else Selectors.BASE_URL.rstrip("/") + href
                        )
                        if full not in categories:
                            categories.append(full)
            except Exception as exc:
                log_exception(self.log, exc, "hover menu niveau 1")

        self.log.info(
            "%d sous-catégorie(s) collectée(s)%s",
            len(categories),
            f" pour « {category_name} »" if filtre else "",
        )
        return categories

    # ─── Listing produits / pagination ────────────────────────────────────────

    async def _get_product_links(self, page: Page, cat_url: str) -> list[str]:
        """Collecte tous les liens produits d'une catégorie (pagination incluse).

        Args:
            page: Page Playwright dédiée à la navigation catalogue.
            cat_url: URL complète de la sous-catégorie.

        Returns:
            Liste d'URLs complètes des fiches produit.
        """
        links: list[str] = []

        await page.goto(cat_url)
        await page.wait_for_load_state("domcontentloaded")

        while True:
            for el in await page.locator(Selectors.product_link).all():
                href = await el.get_attribute("href") or ""
                if href:
                    full = (
                        href if href.startswith("http")
                        else Selectors.BASE_URL.rstrip("/") + href
                    )
                    if full not in links:
                        links.append(full)

            next_el = page.locator(Selectors.pagination_next)
            if await next_el.count() == 0:
                break
            next_href = await next_el.first.get_attribute("href") or ""
            if not next_href:
                break
            full_next = (
                next_href if next_href.startswith("http")
                else Selectors.BASE_URL.rstrip("/") + next_href
            )
            await page.goto(full_next)
            await page.wait_for_load_state("domcontentloaded")

        self.log.info("%d lien(s) produit collecté(s) pour %s", len(links), cat_url)
        return links

    # ─── Helpers extraction page produit ─────────────────────────────────────

    async def _extract_price(self, page: Page, url: str) -> tuple[str, str]:
        """Extrait le prix HT et la promotion éventuelle.

        Logique :
            - Pas de promo : product_price_ht = span.price, promotion = ''
            - Promo active (div.previous-price présent) :
                product_price_ht = prix barré (del), promotion = prix actuel (span.price)

        Returns:
            Tuple (price_ht, promotion). promotion est '' si pas de promo.
        """
        try:
            if await page.locator(Selectors.product_promo_block).count() > 0:
                barre = _clean_price(
                    await page.locator(Selectors.product_price_barre).first.inner_text()
                )
                actuel = _clean_price(
                    await page.locator(Selectors.product_price_actuel).first.inner_text()
                )
                return barre, actuel
            actuel = _clean_price(
                await page.locator(Selectors.product_price_ht).first.inner_text()
            )
            return actuel, ""
        except Exception as exc:
            log_exception(self.log, exc, f"price {url}")
            return "", ""

    async def _extract_attributes(self, page: Page, url: str) -> dict[str, str]:
        """Extrait le tableau des caractéristiques techniques (clé → valeur).

        Itère sur div#tab-caracteristiques tr ; ignore les lignes sans deux colonnes.
        """
        attrs: dict[str, str] = {}
        try:
            for row in await page.locator(Selectors.product_attributes_table).all():
                tds = row.locator("td")
                if await tds.count() >= 2:
                    key = clean_text(await tds.nth(0).inner_text())
                    val = clean_text(await tds.nth(1).inner_text())
                    if key:
                        attrs[key] = val
        except Exception as exc:
            log_exception(self.log, exc, f"attributes {url}")
        return attrs

    async def _extract_eco_taxe(self, page: Page, url: str) -> str:
        """Extrait l'éco-participation depuis la ligne sélectionnée ou la première cellule.

        Priorité : td.article-ecotax dans tr.article.selected (variante active).
        Fallback : première cellule td.article-ecotax de la page.
        Valeur '-' ou vide → retourne ''.
        """
        try:
            selected = page.locator(Selectors.combination_selected_row)
            eco_el = (
                selected.locator("td.article-ecotax")
                if await selected.count() > 0
                else page.locator(Selectors.product_eco_taxe)
            )
            if await eco_el.count() > 0:
                return _clean_eco_taxe(await eco_el.first.inner_text())
        except Exception as exc:
            log_exception(self.log, exc, f"eco_taxe {url}")
        return ""

    async def _extract_eco_label(self, page: Page, url: str) -> str:
        """Extrait les labels éco depuis div.block-product-pictos (matching Python sur alt)."""
        labels: list[str] = []
        try:
            for img in await page.locator(Selectors.product_eco_label).all():
                alt = (await img.get_attribute("alt") or "").strip()
                if "ecolabel" in alt.lower():
                    label = clean_text(alt)
                    if label and label not in labels:
                        labels.append(label)
        except Exception as exc:
            log_exception(self.log, exc, f"eco_label {url}")
        return "||".join(labels)

    async def _extract_combination_values(self, page: Page, url: str) -> str:
        """Extrait les axes de déclinaison depuis tr.article.selected.

        Format de sortie : 'axe1:valeur1||axe2:valeur2'
        """
        values: list[str] = []
        try:
            selected = page.locator(Selectors.combination_selected_row)
            if await selected.count() == 0:
                return ""
            for td in await selected.locator(Selectors.product_combination_attribute).all():
                label = clean_text(await td.get_attribute("data-label") or "")
                value = clean_text(await td.inner_text())
                if label:
                    values.append(f"{label}:{value}")
        except Exception as exc:
            log_exception(self.log, exc, f"combination_values {url}")
        return "||".join(values)

    # ─── Extraction d'une fiche individuelle ─────────────────────────────────

    async def _extract_single_product(self, page: Page, url: str) -> dict:
        """Extrait toutes les données d'une fiche produit ou d'une page variante.

        Ne renseigne pas is_combination, parent_ref, group_refs, combination_index —
        ces champs sont injectés par _get_product_data selon le contexte.

        Returns:
            Dict interne avec les clés utilisées par to_csv_row().
        """
        # Réduit le timeout des opérations locator à 5s (défaut Playwright = 30s).
        # Évite les attentes de 30s par champ sur les pages produits hors-vente.
        # La navigation (goto/wait_for_load_state) garde son timeout via set_default_navigation_timeout.
        page.set_default_timeout(5000)
        page.set_default_navigation_timeout(30000)

        product: dict = {}

        # Désignation
        try:
            product["title"] = clean_text(
                await page.locator(Selectors.product_designation).inner_text()
            )
        except Exception as exc:
            product["title"] = ""
            log_exception(self.log, exc, f"title {url}")

        # Référence fournisseur — attribut data-code, pas inner_text
        try:
            product["ref"] = clean_text(
                await page.locator(Selectors.product_reference_fournisseur)
                .first.get_attribute("data-code") or ""
            )
        except Exception as exc:
            product["ref"] = ""
            log_exception(self.log, exc, f"ref {url}")

        # Prix et promotion
        product["price_ht"], product["promotion"] = await self._extract_price(page, url)

        # Description
        try:
            product["description"] = clean_text(
                await page.locator(Selectors.product_description).inner_text()
            )
        except Exception as exc:
            product["description"] = ""
            log_exception(self.log, exc, f"description {url}")

        # Stock — lecture brute du texte de span.stock
        try:
            stock_el = page.locator(Selectors.product_stock_status)
            product["stock"] = (
                clean_text(await stock_el.first.inner_text())
                if await stock_el.count() > 0 else ""
            )
        except Exception as exc:
            product["stock"] = ""
            log_exception(self.log, exc, f"stock {url}")

        # Statut disponibilité — "1" si pastille verte, "0" sinon
        # Priorité : ligne active tr.article.selected ; fallback : première pastille de la page
        try:
            selected = page.locator(Selectors.combination_selected_row)
            pastille = (
                selected.locator(Selectors.product_status)
                if await selected.count() > 0
                else page.locator(Selectors.product_status)
            )
            if await pastille.count() > 0:
                classes = await pastille.first.get_attribute("class") or ""
                product["status"] = "DISPONIBLE" if "green" in classes else "INDISPONIBLE"
            else:
                product["status"] = "INDISPONIBLE"
        except Exception as exc:
            product["status"] = ""
            log_exception(self.log, exc, f"status {url}")

        # Marque — même sélecteur, alt → nom, src → logo
        try:
            brand_el = page.locator(Selectors.product_brand)
            product["brand"]      = clean_text(await brand_el.get_attribute("alt") or "")
            product["brand_logo"] = await brand_el.get_attribute("src") or ""
        except Exception as exc:
            product["brand"]      = ""
            product["brand_logo"] = ""
            log_exception(self.log, exc, f"brand {url}")

        # Images (attribut src)
        try:
            imgs = [
                src
                for el in await page.locator(Selectors.product_image_url).all()
                if (src := await el.get_attribute("src"))
            ]
            product["images"] = "||".join(imgs)
        except Exception as exc:
            product["images"] = ""
            log_exception(self.log, exc, f"images {url}")

        # Documents (attribut href)
        try:
            docs = [
                href
                for el in await page.locator(Selectors.product_docs_url).all()
                if (href := await el.get_attribute("href"))
            ]
            product["docs"] = "||".join(docs)
        except Exception as exc:
            product["docs"] = ""
            log_exception(self.log, exc, f"docs {url}")

        # Caractéristiques techniques
        try:
            attrs = await self._extract_attributes(page, url)
            product["attributes"] = "||".join(f"{k}:{v}" for k, v in attrs.items())
        except Exception as exc:
            product["attributes"] = ""
            log_exception(self.log, exc, f"attributes {url}")

        # Référence fabricant
        try:
            ref_fab_el = page.locator(Selectors.product_reference_fabricant)
            product["ref_fabricant"] = (
                clean_text(await ref_fab_el.inner_text())
                if await ref_fab_el.count() > 0 else ""
            )
        except Exception as exc:
            product["ref_fabricant"] = ""
            log_exception(self.log, exc, f"ref_fabricant {url}")

        # Conditionnement — strip() obligatoire (espaces parasites constatés dans le HTML)
        try:
            cdt_el = page.locator(Selectors.product_conditionnement)
            product["conditionnement"] = (
                clean_text(await cdt_el.inner_text()).strip()
                if await cdt_el.count() > 0 else ""
            )
        except Exception as exc:
            product["conditionnement"] = ""
            log_exception(self.log, exc, f"conditionnement {url}")

        # Éco-participation
        product["eco_taxe"] = await self._extract_eco_taxe(page, url)

        # Labels éco
        product["eco_label"] = await self._extract_eco_label(page, url)

        # Valeurs de déclinaison (renseignées uniquement sur une page variante)
        product["combination_values"] = await self._extract_combination_values(page, url)

        # Champs combinaisons — remplis par _get_product_data
        product["is_combination"]    = False
        product["combination_index"] = None
        product["parent_ref"]        = product.get("ref", "")
        product["group_refs"]        = product.get("ref", "")

        return product

    # ─── Extraction complète d'un produit (produit simple ou groupe) ──────────

    async def _get_product_data(
        self, page: Page, url: str
    ) -> tuple[bool, list[dict]]:
        """Visite une page produit et extrait toutes ses variantes.

        Si la table des déclinaisons contient plus d'une ligne, navigue vers
        chaque page variante (via button.btn-link[data-src]) et extrait
        les données de chacune individuellement.

        Args:
            page: Page Playwright active.
            url: URL de la fiche produit parente.

        Returns:
            Tuple (has_combo, liste de dicts produit).
        """
        self.log.info("Visite produit : %s", url)
        await page.goto(url)
        await page.wait_for_load_state("domcontentloaded")

        combo_table = page.locator(Selectors.combinations_table)
        rows = await combo_table.locator(Selectors.combinations_row).all()
        self.log.debug("%d ligne(s) de déclinaison sur %s", len(rows), url)

        if len(rows) > 1:
            codes = [
                c.strip()
                for c in await combo_table.locator(Selectors.combinations_codes).all_inner_texts()
                if c.strip()
            ]
            parent_ref = codes[0] if codes else ""
            group_refs = "||".join(codes)
            products: list[dict] = []

            for row in rows:
                try:
                    variant_path = (
                        await row.locator(Selectors.combinations_row_link)
                        .get_attribute("data-src") or ""
                    )
                    if not variant_path:
                        continue
                    full_url = (
                        variant_path if variant_path.startswith("http")
                        else Selectors.BASE_URL.rstrip("/") + variant_path
                    )
                    await page.goto(full_url)
                    await page.wait_for_load_state("domcontentloaded")

                    product = await self._extract_single_product(page, full_url)
                    product["is_combination"] = True
                    product["parent_ref"]     = parent_ref
                    product["group_refs"]     = group_refs
                    products.append(product)
                    self.log.debug("Variante extraite : %s", product.get("ref", "?"))
                except Exception as exc:
                    log_exception(self.log, exc, f"variante {url}")

            return True, products

        product = await self._extract_single_product(page, url)
        return False, [product]

    # ─── Fil d'Ariane ────────────────────────────────────────────────────────

    async def _ariane(self, page: Page) -> tuple[str, str, str]:
        """Extrait les trois niveaux de catégorie depuis le fil d'Ariane.

        Les éléments h6 de ol.breadcrumb sont indexés à partir de 0 (= « Accueil »).
        On prend les indices 1, 2, 3 comme cat1, cat2, cat3.
        """
        try:
            items = await page.locator(Selectors.breadcrumb_items).all()
            cats: list[str] = [
                clean_text(await item.inner_text())
                for item in items[1:4]
            ]
            cats = [c for c in cats if c]
            while len(cats) < 3:
                cats.append("")
            return cats[0], cats[1], cats[2]
        except Exception as exc:
            log_exception(self.log, exc, "ariane")
            return "fail", "fail", "fail"

    # ─── Mapping vers CSV_HEADERS ─────────────────────────────────────────────

    @staticmethod
    def to_csv_row(
        product: dict,
        cat1: str,
        cat2: str,
        cat3: str,
        source_url: str,
    ) -> dict[str, str]:
        """Mappe le dict interne vers les colonnes CSV_HEADERS (core/config.py)."""
        category_tree = "||".join(c for c in (cat1, cat2, cat3) if c and c != "fail")
        is_combo = product.get("is_combination", False)
        ref = product.get("ref", "")
        return {
            "product_fournisseur":           "P6",
            "product_reference_fournisseur": ref,
            "product_ean":                   "",   # n'existe pas sur Sider
            "product_reference_fabricant":   product.get("ref_fabricant", ""),
            "product_brand":                 product.get("brand", ""),
            "product_brand_logo_url":        product.get("brand_logo", ""),
            "product_designation":           product.get("title", ""),
            "product_description":           product.get("description", ""),
            "product_image_url":             product.get("images", ""),
            "product_docs_url":              product.get("docs", ""),
            "product_category_tree":         category_tree,
            "product_conditionnement":       product.get("conditionnement", ""),
            "product_stock_status":          product.get("stock", ""),
            "product_status":                product.get("status", ""),
            "product_fournisseur_url":       source_url,
            "product_eco_label":             product.get("eco_label", ""),
            "product_eco_taxe":              product.get("eco_taxe", ""),
            "product_promotion":             product.get("promotion", ""),
            "product_price_ht":              product.get("price_ht", ""),
            "product_attributes":            product.get("attributes", ""),
            "products_is_combination":       str(is_combo),
            "product_combination_index":     str(product.get("combination_index") or ""),
            "product_parent_reference":      product.get("parent_ref", "") or ref,
            "product_child_reference":       product.get("group_refs", "") or ref,
            "product_combination_values":    product.get("combination_values", ""),
            "product_cross_sell":            "",   # n'existe pas sur Sider
        }
