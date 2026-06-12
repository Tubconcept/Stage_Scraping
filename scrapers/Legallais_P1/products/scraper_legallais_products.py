"""
Moteur CSS du scraper produits Legallais (site P1).

Rôle :
    Encapsule toute l'interaction Botasaurus avec legallais.com : authentification
    par cookies, navigation menu catégories, pagination, extraction des fiches
    produit (prix, stock, images JS, PDF, déclinaisons).

Type : produits.

Architecture :
    - scraper_legallais_products.py (ce fichier) = couche CSS / extraction.
    - scrap_legallais_products.py = orchestrateur (phases collecte + scrape, SQLite).
    Sélecteurs dans selectors/legallais.py ; session via cookie_manager_legallais.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os
import re
from typing import List, Dict, Optional, Tuple

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")


# ─── Classe moteur CSS ────────────────────────────────────────────────────────

class LegallaisScraper:
    """Encapsule toute la logique de navigation et d'extraction Legallais."""

    # Nombre d'articles affichés par page de listing (sélecteur pagination)
    ITEMS_PER_PAGE = 100

    def __init__(self) -> None:
        self._driver = None
        self._email: str  = os.getenv("User_P1", "")
        self._password: str = os.getenv("Password_P1", "")

    # ─── Setup ────────────────────────────────────────────────────────────────

    def set_driver(self, driver) -> None:
        self._driver = driver

    # ─── Authentification ─────────────────────────────────────────────────────

    def connexion(self) -> None:
        from css_selectors.legallais import BASE_URL, LOGIN_URL, SELECTORS
        from auth.legallais.cookie_manager_legallais import (
            load_cookies_for_driver, save_cookies_from_driver,
        )
        d = self._driver

        # Cookies de consentement (toujours injectés)
        d.add_cookies([
            {"name": "CookiesConsent_ads",                     "value": "true", "url": "https://www.legallais.com"},
            {"name": "CookiesConsent_individualCustomization",  "value": "true", "url": "https://www.legallais.com"},
            {"name": "CookiesConsent_required",                "value": "1",    "url": "https://www.legallais.com"},
        ])

        # Tenter de restaurer la session du jour
        if load_cookies_for_driver(d):
            d.get(BASE_URL)
            try:
                d.wait_for_element("a[aria-label='Mon compte'], .o-menu__items__list", 5)
                if not d.is_element_present(SELECTORS["email"]):
                    print("[Legallais] Session restaurée — connexion ignorée.")
                    return
            except Exception:
                pass
            print("[Legallais] Session expirée — nouvelle connexion en cours...")

        # Login complet
        print("[Legallais] Connexion en cours...")
        d.get(LOGIN_URL)
        d.wait_for_element(SELECTORS["email"], 5)
        d.type(SELECTORS["email"], self._email)
        d.type(SELECTORS["password"], self._password)
        d.click(SELECTORS["submit"])
        try:
            d.wait_for_element("a[aria-label='Mon compte'], .o-menu__items__list, nav", 10)
        except Exception:
            pass

        # Sauvegarder la nouvelle session
        save_cookies_from_driver(d)

    # ─── Navigation catégories ────────────────────────────────────────────────

    def get_categories(self, category_filter: Optional[str] = None) -> List[str]:
        """Navigue le menu et retourne la liste des URLs de catégories niveau 3."""
        from css_selectors.legallais import SELECTORS
        d = self._driver
        urls: List[str] = []
        try:
            level1_items = d.select_all(SELECTORS["menu_level1"], 3)
            for l1 in level1_items:
                if category_filter and category_filter.lower() not in l1.text.lower():
                    continue
                try:
                    l1.click()
                    level2_items = d.select_all(SELECTORS["menu_level2"], 2)
                    for l2 in level2_items:
                        try:
                            l2.click()
                            level3_items = d.select_all(SELECTORS["menu_level3"], 2)
                            for l3 in level3_items:
                                href = l3.get_attribute("href")
                                if href:
                                    urls.append(href)
                        except Exception:
                            href = l2.get_attribute("href")
                            if href:
                                urls.append(href)
                except Exception:
                    pass
        except Exception:
            pass
        return urls

    # ─── Pagination ───────────────────────────────────────────────────────────

    def set_items_per_page(self) -> None:
        from css_selectors.legallais import SELECTORS
        d = self._driver
        try:
            d.wait_for_element(SELECTORS["pagination_items_per_page"], 3)
            d.select_option(SELECTORS["pagination_items_per_page"], str(self.ITEMS_PER_PAGE))
            d.wait_for_element(SELECTORS["product_card"], 3)
        except Exception:
            pass

    def get_page_count(self) -> int:
        from css_selectors.legallais import SELECTORS
        d = self._driver
        try:
            options = d.select_all(SELECTORS["pagination_options"], 2)
            if options:
                return len(options)
        except Exception:
            pass
        return 1

    def go_to_page(self, page_num: int) -> None:
        from css_selectors.legallais import SELECTORS
        d = self._driver
        try:
            d.select_option(SELECTORS["pagination_select"], str(page_num))
            d.wait_for_element(SELECTORS["product_card"], 3)
        except Exception:
            pass

    # ─── Fil d'Ariane (catégories) ────────────────────────────────────────────

    def get_breadcrumb_categories(self) -> Tuple[str, str, str]:
        from css_selectors.legallais import SELECTORS
        d = self._driver
        cat1 = cat2 = cat3 = ""
        try:
            # breadcrumb_items sélectionne les <li> individuels : [Accueil, Cat1, Cat2, Cat3]
            crumbs = d.select_all(SELECTORS["breadcrumb_items"], 2)
            if len(crumbs) > 1:
                cat1 = crumbs[1].text.strip()
            if len(crumbs) > 2:
                cat2 = crumbs[2].text.strip()
            if len(crumbs) > 3:
                cat3 = crumbs[3].text.strip()
        except Exception:
            pass
        return cat1, cat2, cat3

    # ─── Listing produits ─────────────────────────────────────────────────────

    def get_product_links(self) -> List[str]:
        from css_selectors.legallais import SELECTORS
        d = self._driver
        links: List[str] = []
        try:
            cards = d.select_all(SELECTORS["product_link"], 2)
            for card in cards:
                href = card.get_attribute("href")
                if href:
                    links.append(href)
        except Exception:
            pass
        return links

    # ─── Extraction page produit ──────────────────────────────────────────────

    def scrape_product(self, group_index: int = 1) -> List[Dict]:
        from css_selectors.legallais import SELECTORS
        from core.utils import clean_text
        d = self._driver
        rows: List[Dict] = []

        # Référence produit
        product_ref = ""
        try:
            ref_el = d.select(SELECTORS["product_reference"], 1)
            if ref_el:
                product_ref = re.sub(r"\D", "", ref_el.text).strip()
        except Exception:
            pass

        # Titre
        title = ""
        try:
            title = clean_text(d.select(SELECTORS["product_title"], 1).text)
        except Exception:
            pass

        # Prix HT — valeur numérique uniquement (ex: "15.48")
        # Essai sur le sous-élément précis, puis fallback sur le conteneur entier
        price = ""
        try:
            price_el = None
            for _psel in [SELECTORS["price_final"],
                          "div.c-price.c-price--final",
                          ".c-price--final"]:
                try:
                    price_el = d.select(_psel, 1)
                    if price_el:
                        break
                except Exception:
                    continue
            if price_el:
                raw_price = price_el.text.replace("€", "").replace("\xa0", " ")
                m = re.search(r"(\d[\d ]*[.,]\d+|\d+)", raw_price)
                price = m.group(1).replace(",", ".").replace(" ", "") if m else ""
        except Exception:
            pass

        # Prix barré original
        price_original = ""
        try:
            price_original = clean_text(d.select(SELECTORS["price_original"], 1).text)
        except Exception:
            pass

        # Eco-participation
        price_eco = ""
        try:
            price_eco = clean_text(d.select(SELECTORS["price_eco"], 1).text)
        except Exception:
            pass

        # Stock
        stock = ""
        try:
            stock = clean_text(d.select(SELECTORS["stock"], 1).text)
        except Exception:
            pass

        # Image marque
        brand_img = ""
        try:
            brand_img = d.select(SELECTORS["brand_image"], 1).get_attribute("src") or ""
        except Exception:
            pass

        # Images produit — extraction JS multi-stratégie (slides → galerie → fallback)
        images: List[str] = []
        try:
            result = d.run_js("""
                const ATTRS   = ['data-zoom','data-src','data-original','data-large','data-full'];
                const QUERIES = [
                    'div.c-photo-gallery__slide img',
                    'div.c-photo-gallery__main img',
                    '.c-photo-gallery img',
                    'div[class*="gallery"] img',
                    'div[class*="photo"] img'
                ];
                const SKIP = ['placeholder','logo','icon','thumb','sprite'];
                const seen = new Set();
                for (const q of QUERIES) {
                    const imgs = document.querySelectorAll(q);
                    if (!imgs.length) continue;
                    const results = [];
                    for (const img of imgs) {
                        let src = '';
                        for (const a of ATTRS) { src = img.getAttribute(a) || ''; if (src) break; }
                        if (!src) src = img.src || '';
                        if (src && !SKIP.some(s => src.toLowerCase().includes(s)) && !seen.has(src)) {
                            seen.add(src);
                            results.push(src);
                        }
                    }
                    if (results.length) return results;
                }
                return [];
            """)
            images = result if isinstance(result, list) else []
        except Exception:
            images = []

        # Description
        desc = ""
        try:
            desc = d.select(SELECTORS["description"], 1).text
        except Exception:
            pass

        # Documents PDF — extraction JS
        # Gère : href direct .pdf  ET  href="javascript:window.open('url.pdf', ...)"
        docs: List[str] = []
        try:
            result = d.run_js("""
                const results = [];
                const pdfRe = /window\\.open\\s*\\(\\s*['"]([^'"]+\\.pdf)['"]/i;
                for (const a of document.querySelectorAll('a')) {
                    const raw  = a.getAttribute('href') || '';
                    const onclick = a.getAttribute('onclick') || '';

                    // Cas 1 : href direct PDF (absolu résolu par le navigateur)
                    if (raw && !raw.startsWith('javascript') && raw.toLowerCase().includes('.pdf')) {
                        const full = a.href;
                        if (!results.includes(full)) results.push(full);
                        continue;
                    }

                    // Cas 2 : href="javascript:window.open('url.pdf', ...)"
                    if (raw.startsWith('javascript')) {
                        const m = raw.match(pdfRe);
                        if (m && !results.includes(m[1])) { results.push(m[1]); continue; }
                    }

                    // Cas 3 : onclick="window.open('url.pdf', ...)"
                    if (onclick) {
                        const m = onclick.match(pdfRe);
                        if (m && !results.includes(m[1])) results.push(m[1]);
                    }
                }
                return results;
            """)
            docs = result if isinstance(result, list) else []
        except Exception:
            docs = []

        # Eco-labels engagement — URLs absolues des images SVG/PNG de labels
        eco_labels: List[str] = []
        try:
            result = d.run_js("""
                const BASE = 'https://www.legallais.com';
                const QUERIES = [
                    '#piano img',
                    'div.c-configurator-search-card__content img',
                    'div[class*="engagement"] img',
                    'div[class*="label"] img'
                ];
                const seen = new Set();
                const results = [];
                for (const q of QUERIES) {
                    for (const img of document.querySelectorAll(q)) {
                        let src = img.getAttribute('src') || img.src || '';
                        if (!src) continue;
                        if (!src.startsWith('http')) {
                            src = BASE + (src.startsWith('/') ? src : '/' + src);
                        }
                        if (!seen.has(src)) { seen.add(src); results.push(src); }
                    }
                }
                return results;
            """)
            eco_labels = result if isinstance(result, list) else []
        except Exception:
            eco_labels = []

        # Conditionnement — extrait de la ligne "Conditionnement" du tableau des caractéristiques
        conditionnement = ""
        try:
            result = d.run_js("""
                const rows = document.querySelectorAll('#characteristicsTable tr');
                for (const row of rows) {
                    const cells = row.querySelectorAll('th, td');
                    for (let i = 0; i < cells.length - 1; i++) {
                        if (cells[i].textContent.trim().toLowerCase().includes('conditionnement')) {
                            return cells[i + 1].textContent.trim();
                        }
                    }
                }
                return '';
            """)
            conditionnement = (result or "").strip()
        except Exception:
            conditionnement = ""

        # Caractéristiques (attributs, séparateur ||)
        attrs: List[str] = []
        ref_fab = ""
        try:
            char_rows = d.select_all(SELECTORS["characteristics_table"], 1)
            for char_row in char_rows:
                tds = char_row.select_all("td", 0)
                if len(tds) >= 2:
                    label = " ".join(tds[0].text.split()).strip()
                    value = " ".join(tds[1].text.split()).strip()
                    if label and value:
                        attrs.append(f"{label}={value}")
        except Exception:
            pass
        # Référence fabricant — sélectionne la ligne tr#characCodeProvider entière
        try:
            ref_row = d.select(SELECTORS["characteristics_ref"], 1)
            if ref_row:
                tds = ref_row.select_all("td", 0)
                if tds:
                    ref_fab = clean_text(tds[-1].text)
        except Exception:
            pass
        # Fallback ref fabricant via sélecteur dédié
        if not ref_fab:
            try:
                el = d.select(SELECTORS["supplier_ref"], 1)
                if el:
                    ref_fab = clean_text(el.text)
            except Exception:
                pass

        # EAN
        ean = ""
        try:
            ean_el = d.select(SELECTORS["product_ean"], 1)
            if ean_el:
                ean = clean_text(ean_el.text)
        except Exception:
            pass

        # "Articles de la même gamme" — référence extraite de l'alt de l'image (ex: alt="wi341762" → 341762)
        cross_sell_refs: List[str] = []
        try:
            result = d.run_js("""
                const refs = [];
                const GAMME = /m[eê]me\\s+gamme|articles?\\s+associ[eé]s?|cross.?sell/i;
                const sections = new Set();

                // Conteneurs nommés connus
                for (const sel of [
                    'div.cross-sell', 'div[class*="gamme"]', 'section[class*="gamme"]',
                    'div[class*="associated"]', 'section[class*="associated"]',
                    'div[class*="similar"]', 'div[class*="related"]'
                ]) {
                    for (const el of document.querySelectorAll(sel)) sections.add(el);
                }

                // Recherche par titre de section si aucun conteneur trouvé
                if (!sections.size) {
                    for (const hd of document.querySelectorAll('h2, h3, h4, .section-title')) {
                        if (GAMME.test(hd.textContent.trim())) {
                            const parent = hd.closest('section, div.c-card, article') || hd.parentElement?.parentElement;
                            if (parent) sections.add(parent);
                        }
                    }
                }

                for (const section of sections) {
                    for (const img of section.querySelectorAll('img')) {
                        const alt = img.getAttribute('alt') || '';
                        const digits = alt.replace(/\\D/g, '').trim();
                        if (digits && digits.length >= 4 && !refs.includes(digits)) {
                            refs.push(digits);
                        }
                    }
                }
                return refs;
            """)
            cross_sell_refs = result if isinstance(result, list) else []
        except Exception:
            cross_sell_refs = []

        # Nettoyage : supprimer le suffixe après "_" (ex: "693165_3" → "693165")
        cleaned: List[str] = []
        for ref in cross_sell_refs:
            clean_ref = ref.split("_")[0] if "_" in ref else ref
            if clean_ref and clean_ref not in cleaned:
                cleaned.append(clean_ref)
        cross_sell_refs = cleaned

        # Nom de marque (alt de l'image logo)
        brand_name = ""
        try:
            brand_el = d.select(SELECTORS["brand_image"], 1)
            if brand_el:
                brand_name = (brand_el.get_attribute("alt") or
                              brand_el.get_attribute("title") or "")
                brand_name = brand_name.strip()
        except Exception:
            pass

        base_row = {
            "productRef":            product_ref,
            "productTitle":          title,
            "productPrice":          price,
            "price_original":        price_original,
            "price_eco":             price_eco,
            "stockStatus":           stock,
            "productBrand":          brand_name,
            "Image_Brand":           brand_img,
            "productImages":         "||".join(images),
            "productDesc":           desc,
            "productDocList":        "||".join(docs),
            "ecoLabel":              "||".join(eco_labels),
            "conditionnement":       conditionnement,
            "productAttributes":     "||".join(attrs),
            "Ref_fabricant":         ref_fab,
            "EAN":                   ean,
            "ProductUrl":            d.current_url,
            "parentRef":             product_ref,
            "childRefs":             product_ref,   # sera mis à jour si combinaisons trouvées
            "crossSell":             "||".join(cross_sell_refs),
        }

        # Déclinaisons (tableau de combinaisons)
        try:
            combo_rows = d.select_all(SELECTORS["combinations_table"], 1)
            if combo_rows:
                headers: List[str] = []
                try:
                    header_els = d.select_all(SELECTORS["combinations_headers"], 1)
                    headers = [h.text.strip() for h in header_els]
                except Exception:
                    pass

                # 1re passe : collecte toutes les refs enfants pour product_child_reference
                all_child_refs: List[str] = [product_ref] if product_ref else []
                for combo in combo_rows:
                    tds = combo.select_all("td", 0)
                    if tds:
                        ref_td = clean_text(tds[0].text)
                        if ref_td and ref_td not in all_child_refs:
                            all_child_refs.append(ref_td)
                child_refs_str = "||".join(all_child_refs)
                base_row["childRefs"] = child_refs_str

                # 2e passe : extraction complète de chaque déclinaison
                for idx, combo in enumerate(combo_rows, 1):
                    tds = combo.select_all("td", 0)
                    row = dict(base_row)
                    row["isCombination"]       = "True"
                    row["combinationIndex"]    = group_index
                    row["parentRef"]           = base_row["productRef"]
                    row["childRefs"]           = child_refs_str
                    if tds:
                        ref_td = clean_text(tds[0].text)
                        if ref_td:
                            row["productRef"] = ref_td
                        # Format : "Header=Valeur||Header=Valeur"
                        # Nettoyage sans clean_text pour ne pas corrompre = et :
                        decli_parts = []
                        for h, td in zip(headers[1:], tds[1:]):
                            val = td.text.replace("\xa0", " ").replace("\n", " ").replace("\r", "").replace("\t", " ")
                            val = val.replace("AJOUTER", "").replace("Ajouter", "").replace("ajouter", "")
                            val = " ".join(val.split()).strip()
                            if not val:
                                continue
                            hdr = " ".join(h.strip().rstrip(":").split())
                            decli_parts.append(f"{hdr}={val}")
                        row["productDecliName&Value"] = "||".join(decli_parts)
                    rows.append(row)
            else:
                base_row["isCombination"] = "False"
                base_row["combinationIndex"] = group_index
                rows.append(base_row)
        except Exception:
            base_row["isCombination"] = "False"
            base_row["combinationIndex"] = group_index
            rows.append(base_row)

        return rows
