"""
Moteur CSS du scraper produits Sonepar (site P8).

Rôle :
    Extraction pure depuis le DOM Sonepar : connexion, navigation dans le mega-menu,
    pagination, parsing des fiches produit et de leurs variantes.

Type : produits.

Architecture :
    - scraper_sonepar_products.py (ce fichier) = couche CSS / parsing HTML.
    - scrap_sonepar_products.py = orchestrateur (run, MariaDB, reprise, CLI).
    Aucune écriture en base ici : les données sont renvoyées via to_csv_row().

Sélecteurs centralisés dans css_selectors/sonepar.py (classe Selectors).
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os

from dotenv import load_dotenv
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from css_selectors.sonepar import Selectors
from core.base_scraper import BaseScraper
from core.config import TIMEOUT_PAGE_LOAD
from core.logger import log_exception

load_dotenv()

# ─── Normalisation du texte ────────────────────────────────────────────────────

_MULTI_REPLACEMENTS = [
    (">=", " supérieur ou égal à "),
    ("<=", " inférieur ou égal à "),
    (">",  " supérieur à "),
    ("<",  " inférieur à "),
    ("=",  " égal à "),
]

_TRANS = str.maketrans({
    "\xa0": " ", "\n": " ", "\r": None, "\t": " ",
    ":": None, ",": ".", ";": ",", "/": "-", '"': " ",
})


def _clean(texte: str) -> str:
    if not isinstance(texte, str):
        return texte
    texte = html.unescape(texte)
    for old, new in _MULTI_REPLACEMENTS:
        texte = texte.replace(old, new)
    texte = texte.translate(_TRANS)
    return " ".join(texte.split()).strip()


# ─── Classe moteur CSS ────────────────────────────────────────────────────────

class _SoneparCSS(BaseScraper):
    """Moteur CSS du catalogue produits Sonepar — sélecteurs, parsing, extraction HTML."""

    SUPPLIER: str = "sonepar"

    def __init__(self) -> None:
        super().__init__("sonepar_products")
        self._username: str = os.getenv("User_P8", "")
        self._password: str = os.getenv("Password_P8", "")
        if not self._username or not self._password:
            self.log.warning("User_P8 ou Password_P8 non défini dans .env")

    # ─── Connexion / session ──────────────────────────────────────────────────

    async def _is_logged_in(self, page: Page) -> bool:
        """Retourne True si connecté (bouton login absent)."""
        try:
            return await page.locator(Selectors.login_button).count() == 0
        except Exception:
            return True

    async def _connexion(self, page: Page) -> None:
        """Effectue la connexion au compte Sonepar."""
        await page.locator(Selectors.login_button).click()
        # Attendre que le formulaire de login soit bien visible
        await page.wait_for_selector(Selectors.email_input, timeout=15000)
        await page.wait_for_timeout(1000)
        await page.locator(Selectors.email_input).fill(self._username)
        await page.wait_for_timeout(500)
        await page.locator(Selectors.password_input).fill(self._password)
        await page.wait_for_timeout(500)
        await page.locator(Selectors.submit).click()
        # Laisser le temps à l'authentification de se terminer côté serveur
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(4000)

    async def _ensure_logged(self, page: Page) -> None:
        """Reconnecte si la session a expiré ou si le prix est absent."""
        if not await self._is_logged_in(page):
            self.log.info("Session expirée → reconnexion")
            await self._connexion(page)
            await page.wait_for_timeout(3000)

    # ─── Navigation catalogue ─────────────────────────────────────────────────

    async def _get_categories(
        self, page: Page, cat_filter: list[str] | None = None
    ) -> list[tuple[str, str, str]]:
        """Parcourt le mega-menu et retourne les (T1, T2, url) des sous-catégories.

        T1 = nom exact du bouton T1 dans le mega-menu (= catégorie choisie par l'utilisateur).
        T2 = nom du lien de niveau 2.
        url = URL complète de la page catégorie.

        cat_filter : si fourni, seules les catégories T1 dont le nom contient
                     un élément de la liste sont incluses.
        """
        results: list[tuple[str, str, str]] = []

        await page.goto(Selectors.BASE_URL)
        await page.wait_for_load_state("domcontentloaded")

        try:
            await page.locator(Selectors.cookie_accept_button).click(timeout=3000)
        except Exception:
            pass

        await page.locator(Selectors.menu_mega_button).click()
        await page.wait_for_selector(Selectors.category_level1_button)
        buttons = await page.locator(Selectors.category_level1_button).all()

        for btn in buttons[:10]:
            try:
                t1_name = (await btn.inner_text()).strip()
                if cat_filter and not any(
                    sel.strip().lower() in t1_name.lower() for sel in cat_filter
                ):
                    continue
                await btn.click()
                panel = page.locator(Selectors.mega_panel_content)
                links = await panel.locator(Selectors.category_level2_links).all()
                for link in links:
                    try:
                        t2_name = (await link.inner_text()).strip()
                        href = await link.get_attribute("href")
                        if href:
                            results.append((
                                t1_name,
                                t2_name,
                                Selectors.BASE_URL.rstrip("/") + href,
                            ))
                    except Exception as exc:
                        log_exception(self.log, exc, f"Lien catégorie '{t1_name}'")
                await page.locator(Selectors.mega_panel_back_button).click()
                await page.wait_for_timeout(500)
            except Exception as exc:
                log_exception(self.log, exc, "Navigation catégorie T1")

        self.log.info("Catégories trouvées : %d", len(results))
        return results

    async def _get_categories_from_breadcrumb(
        self, page: Page
    ) -> tuple[str, str, str]:
        """Extrait T1, T2, T3 depuis le fil d'Ariane de la page produit.

        Structure Sonepar : Accueil > T1 > T2 > T3 > [Nom produit non-lien]
        Seuls les <a> sont sélectionnés, donc le nom du produit (dernier <li>
        sans lien) est automatiquement exclu.
        """
        try:
            texts: list[str] = await page.evaluate("""
                () => {
                    const items = [...document.querySelectorAll('ol.watts-breadcrumb__list li')]
                        .map(li => li.textContent.trim())
                        .filter(t => t && t.toLowerCase() !== 'accueil');
                    // Le dernier item est toujours le nom du produit (page courante) — on l'exclut
                    return items.slice(0, -1);
                }
            """)
            t1 = texts[0].strip() if len(texts) >= 1 else ""
            t2 = texts[1].strip() if len(texts) >= 2 else ""
            t3 = texts[2].strip() if len(texts) >= 3 else ""
            return t1, t2, t3
        except Exception:
            return "", "", ""

    async def _collect_product_links(self, page: Page, cat_url: str) -> list[str]:
        """Collecte les hrefs relatifs de tous les produits d'une catégorie (avec pagination)."""
        product_links: list[str] = []

        await page.goto(cat_url)
        await page.wait_for_load_state("domcontentloaded")
        # Attendre le rendu JavaScript (produits chargés de façon asynchrone)
        await page.wait_for_timeout(2500)

        while True:
            # Évaluation JavaScript : cherche via classe partielle ET href — plus robuste
            # que les sélecteurs CSS Playwright face aux noms de classes Next.js générés
            hrefs: list[str] = await page.evaluate(r"""
                () => {
                    const seen = new Set();
                    const results = [];
                    // Stratégie 1 : class contenant 'productLink' (nom de classe stable)
                    document.querySelectorAll('a[class*="productLink"]').forEach(a => {
                        const h = a.getAttribute('href');
                        if (h && !seen.has(h)) { seen.add(h); results.push(h); }
                    });
                    // Stratégie 2 : tout lien contenant /products/ dans l'href (fallback)
                    if (results.length === 0) {
                        document.querySelectorAll('a[href]').forEach(a => {
                            const h = a.getAttribute('href');
                            if (h && h.includes('/products/') && !seen.has(h)) {
                                seen.add(h); results.push(h);
                            }
                        });
                    }
                    return results;
                }
            """)

            for href in hrefs:
                if not href:
                    continue
                # Normaliser : extraire le chemin depuis une URL absolue si nécessaire
                if href.startswith("http"):
                    try:
                        from urllib.parse import urlparse
                        href = urlparse(href).path
                    except Exception:
                        continue
                if "/products/" in href and href not in product_links:
                    product_links.append(href)

            next_btn = page.locator(Selectors.pagination_next)
            if await next_btn.count() == 0:
                break
            await next_btn.first.click()
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(1200)

        return product_links

    # ─── Extraction produit ───────────────────────────────────────────────────

    async def _safe_goto_product(self, page: Page, url: str, max_retry: int = 2) -> bool:
        """Navigue vers une fiche produit avec retry. Retourne False si échec."""
        for attempt in range(1, max_retry + 1):
            try:
                await page.goto(url, timeout=TIMEOUT_PAGE_LOAD)
                await page.wait_for_load_state("domcontentloaded")
                if await page.locator(Selectors.product_title).count() == 0:
                    raise Exception("Page produit sans titre")
                return True
            except Exception as exc:
                log_exception(
                    self.log, exc, f"_safe_goto_product attempt {attempt}: {url}"
                )
                if attempt < max_retry:
                    await page.reload()
                    await page.wait_for_timeout(2000)
        return False

    async def _get_all_variant_refs(
        self, page: Page
    ) -> list[tuple[str, str]]:
        """Extrait les variantes du groupe depuis le sélecteur de variantes.

        Returns:
            Liste de (ref_11_chiffres, chemin_complet) — ex: ('00014003355', '/products/00014003355-fil-rigide-vu')
            Le chemin_complet est conservé pour construire des URL valides (avec slug).
        """
        result: list[tuple[str, str]] = []
        seen_refs: set[str] = set()
        try:
            variant_links = page.locator(Selectors.variant_picker_link)
            count = await variant_links.count()
            for i in range(count):
                href = await variant_links.nth(i).get_attribute("href")
                if not href:
                    continue
                # Normaliser en chemin relatif si URL absolue
                if href.startswith("http"):
                    from urllib.parse import urlparse
                    path = urlparse(href).path
                else:
                    path = href
                slug = path.split("/products/")[-1]
                # La référence Sonepar est toujours 11 chiffres en début de slug
                m = re.match(r"^(\d{11})", slug)
                ref = m.group(1) if m else slug.split("-")[0]
                if ref and ref not in seen_refs:
                    seen_refs.add(ref)
                    result.append((ref, path))
        except Exception as exc:
            log_exception(self.log, exc, "_get_all_variant_refs")
        return result

    async def _get_related_product_refs(self, page: Page) -> list[str]:
        """Extrait les références Sonepar (11 chiffres) de la section 'Produits associés'.

        Utilise une évaluation JS pour chercher le titre exact 'Produits associés'
        (exclut 'Fréquemment achetés ensemble') puis extrait 'Réf. XXXXXXXXXXX'.
        """
        try:
            return await page.evaluate(r"""
                () => {
                    // Trouver la section dont le titre exact est "Produits associés"
                    const headings = document.querySelectorAll('h2, h3, h4, p, span');
                    let container = null;
                    for (const h of headings) {
                        if (h.textContent.trim() === 'Produits associés') {
                            container = h.closest('section')
                                || h.closest('div[class*="related"]')
                                || h.closest('[data-testid]')
                                || h.parentElement?.parentElement;
                            break;
                        }
                    }
                    if (!container) return [];
                    // "Réf. XXXXXXXXXXX" (espace après le point, pas "Réf.fab")
                    const refs = new Set();
                    for (const m of container.textContent.matchAll(/Réf\.\s+(\d{11})/g)) {
                        refs.add(m[1]);
                    }
                    return [...refs];
                }
            """)
        except Exception as exc:
            log_exception(self.log, exc, "_get_related_product_refs")
            return []

    async def _get_product_data(
        self, page: Page, url: str
    ) -> tuple[bool, list[dict]]:
        """Extrait toutes les données d'une fiche produit Sonepar.

        Returns:
            (has_combo, [product_dict])
            IndexCombination est laissé à None — attribué par l'orchestrateur.
        """
        title = marque = marque_img = tree = stock = cdt = ""
        prix = product_ref = ean = four = actif = ""
        description = ""
        docs: list[str] = []
        img: list[str] = []
        name_var: list[str] = []
        caracteristiques: dict[str, str] = {}
        combination_values: list[str] = []
        is_combination = False
        parent = ""
        ref_lier = ""
        variant_paths: list[str] = []
        related_refs: list[str] = []

        # Titre
        try:
            title = _clean(
                await page.locator(Selectors.product_title).inner_text()
            )
        except Exception as exc:
            log_exception(self.log, exc, f"Titre {url}")

        # Marque
        try:
            img_loc = page.locator(Selectors.product_brand_block)
            marque = await img_loc.get_attribute("aria-label") or ""
            marque_img = await img_loc.get_attribute("src") or ""
        except Exception as exc:
            log_exception(self.log, exc, f"Marque {url}")

        # Arborescence catégorie
        try:
            tree = await self._get_tree(page)
        except Exception:
            pass

        # Stock — sélecteur data-testid stable, normalisation en 3 valeurs
        try:
            stock_loc = page.locator(Selectors.product_stock_status)
            if await stock_loc.count() > 0:
                stock_raw = _clean(await stock_loc.first.inner_text())
                s = stock_raw.lower()
                if "en stock" in s:
                    stock = "EN STOCK"
                elif "non retournable" in s:
                    stock = stock_raw          # conserver la phrase exacte
                else:
                    stock = "PAS EN STOCK"
            else:
                stock = ""
        except Exception as exc:
            log_exception(self.log, exc, f"Stock {url}")

        # Statut actif / fin de vie (vide si produit normal, "False" si fin de vie)
        try:
            actif_loc = page.locator(Selectors.product_active_tag).first
            if await actif_loc.count() > 0:
                actif_text = _clean(await actif_loc.inner_text())
                actif = "False" if actif_text.lower() == "fin de vie" else ""
            else:
                actif = ""
        except Exception as exc:
            log_exception(self.log, exc, f"Actif {url}")
            actif = ""

        # Conditionnement
        try:
            cdt_raw = await page.locator(Selectors.product_conditionnement).inner_text()
            cdt = _clean(cdt_raw.replace("unité(s)", "").strip())
        except Exception as exc:
            log_exception(self.log, exc, f"Conditionnement {url}")

        # Accordéon Références
        try:
            await page.locator(Selectors.accordion_reference_button).click()
            await page.wait_for_selector(Selectors.accordion_reference_content)
            ref_sec = page.locator(Selectors.accordion_reference_content)

            try:
                ean = _clean(
                    await ref_sec.locator(Selectors.product_ean).inner_text(timeout=30000)
                )
            except Exception as exc:
                log_exception(self.log, exc, f"EAN {url}")

            # Réf commerciale non stockée dans CSV_HEADERS — lecture ignorée


            try:
                four = _clean(
                    await ref_sec.locator(
                        Selectors.product_reference_fabricant
                    ).inner_text(timeout=30000)
                )
            except Exception as exc:
                log_exception(self.log, exc, f"Réf fabricant {url}")

            try:
                section = page.locator(Selectors.product_card_icons_section)
                if await section.count() > 0:
                    data_testid = await section.first.get_attribute("data-testid") or ""
                    product_ref = data_testid.split("-")[-1]
            except Exception as exc:
                log_exception(self.log, exc, f"ProductRef {url}")

            try:
                prix = _clean(
                    await page.locator(Selectors.product_price).inner_text(timeout=30000)
                )
            except Exception as exc:
                log_exception(self.log, exc, f"Prix {url}")

        except Exception as exc:
            log_exception(self.log, exc, f"Références {url}")

        # Accordéon Description
        try:
            await page.locator(Selectors.accordion_detail_button).click()
            description = _clean(
                await page.locator(Selectors.product_description).inner_text(timeout=5000)
            )
        except Exception as exc:
            log_exception(self.log, exc, f"Description {url}")

        # Accordéon Documentation
        try:
            doc_btn = page.locator(Selectors.accordion_document_button)
            if await doc_btn.count() > 0:
                await doc_btn.click()
                docs = [
                    href
                    for a in await page.locator(Selectors.product_docs_links).all()
                    if (href := await a.get_attribute("href"))
                ]
        except Exception as exc:
            log_exception(self.log, exc, f"Documents {url}")

        # Images
        try:
            img = [
                src
                for im in await page.locator(Selectors.product_images_carousel).all()
                if (src := await im.get_attribute("src"))
            ]
        except Exception as exc:
            log_exception(self.log, exc, f"Images {url}")

        # Noms des axes de variantes
        try:
            name_raw = await page.locator(
                Selectors.variant_name_label
            ).all_inner_texts()
            name_var = [n.replace(":", "") for n in name_raw]
        except Exception as exc:
            log_exception(self.log, exc, f"Noms variantes {url}")

        # Accordéon Caractéristiques techniques
        try:
            await page.locator(Selectors.accordion_techspec_button).click()
            specs = await page.locator(Selectors.product_techspec_item).all()
            for spec_row in specs:
                ps = await spec_row.locator(
                    Selectors.product_techspec_label_value
                ).all()
                if len(ps) >= 2:
                    name_ = _clean(await ps[0].inner_text(timeout=5000))
                    value_ = _clean(await ps[-1].inner_text(timeout=5000))
                    if name_ and name_ not in name_var:
                        caracteristiques[name_] = value_
        except Exception as exc:
            log_exception(self.log, exc, f"Caractéristiques {url}")

        # Déclinaisons / variantes
        if name_var:
            try:
                is_combination = True
                variant_data = await self._get_all_variant_refs(page)
                if variant_data:
                    variant_refs_list = [r for r, _ in variant_data]
                    variant_paths = [p for _, p in variant_data]
                    parent = variant_refs_list[0]
                    ref_lier = "|".join(variant_refs_list)

                value_vars = await page.locator(
                    Selectors.variant_value_selectable
                ).all()
                for idx, value_loc in enumerate(value_vars):
                    if idx >= len(name_var):
                        break
                    val_text = _clean(await value_loc.inner_text())
                    combination_values.append(f"{name_var[idx]} {val_text}")

            except Exception as exc:
                log_exception(self.log, exc, f"Déclinaisons {url}")

        # Produits associés (cross-sell)
        related_refs = await self._get_related_product_refs(page)

        product = {
            "référence":       product_ref,
            "title":           title,
            "four":            four,
            "ean":             ean,
            "description":     description,
            "image":           "||".join(img) if img else "",
            "doc":             "||".join(docs) if docs else "",
            "caractéristiques": "||".join(
                f"{k}:{v}" for k, v in caracteristiques.items()
            ),
            "conditionnement": cdt,
            "CombinationValues": "||".join(combination_values),
            "IsCombination":   is_combination,
            "IndexCombination": None,   # attribué par l'orchestrateur
            "parent":          parent,
            "ref_lier":        ref_lier,
            "variant_paths":   "|".join(variant_paths),
            "marque":          marque,
            "marqueLink":      marque_img,
            "tree":            tree,
            "prix":            prix,
            "stockStatus":     stock,
            "actif":           actif,
            "related":         "||".join(related_refs) if related_refs else "",
        }

        return is_combination, [product]

    # ─── Mapping vers CSV_HEADERS ─────────────────────────────────────────────

    def to_csv_row(
        self,
        product: dict,
        cat1: str,
        cat2: str,
        cat3: str,
        source_url: str,
    ) -> dict:
        """Convertit un dict produit interne en ligne conforme à CSV_HEADERS."""
        # T1, T2, T3 lus depuis le fil d'Ariane de la page produit
        tree = "||".join(filter(None, [cat1, cat2, cat3]))

        own_ref = product.get("référence", "")
        ref_lier = product.get("ref_lier", "")
        parent_ref = product.get("parent", "")

        if product.get("IsCombination") and ref_lier and parent_ref:
            # Groupe de combinaisons : parent = premier ref, enfants = les autres
            all_refs = [r for r in ref_lier.split("|") if r]
            child_refs = [r for r in all_refs if r != parent_ref]
            parent_reference = parent_ref
            child_reference = " || ".join([parent_ref] + child_refs)
        else:
            # Produit seul : parent et child = sa propre référence
            parent_reference = own_ref
            child_reference = own_ref

        return {
            "product_fournisseur":             "P8",
            "product_reference_fournisseur":   own_ref,
            "product_ean":                     product.get("ean", ""),
            "product_reference_fabricant":     product.get("four", ""),
            "product_brand":                   product.get("marque", ""),
            "product_brand_logo_url":          product.get("marqueLink", ""),
            "product_designation":             product.get("title", ""),
            "product_description":             product.get("description", ""),
            "product_image_url":               product.get("image", ""),
            "product_docs_url":                product.get("doc", ""),
            "product_category_tree":           tree,
            "product_conditionnement":         product.get("conditionnement", ""),
            "product_stock_status":            product.get("stockStatus", ""),
            "product_status":                  product.get("actif", ""),
            "product_fournisseur_url":         source_url,
            "product_eco_label":               "",
            "product_eco_taxe":                "",
            "product_promotion":               "",
            "product_price_ht":                product.get("prix", ""),
            "product_attributes":              product.get("caractéristiques", ""),
            "products_is_combination":         "true" if product.get("IsCombination") else "false",
            "product_combination_index":       str(product.get("IndexCombination") or ""),
            "product_parent_reference":        parent_reference,
            "product_child_reference":         child_reference,
            "product_combination_values":      product.get("CombinationValues", ""),
            "product_cross_sell":              product.get("related", ""),
        }
