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

URL_SITE = "https://www.legallais.com"


# ─── Helpers navigation catégories (non décorés → testables) ─────────────────

def _collect_l3_urls(l2, d, selectors: dict, urls: list) -> None:
    """Click l2, collect level-3 hrefs; fall back to l2 href if level-3 fails."""
    try:
        l2.click()
        for l3 in d.select_all(selectors["menu_level3"], 2):
            href = l3.get_attribute("href")
            if href:
                urls.append(href)
    except Exception:
        href = l2.get_attribute("href")
        if href:
            urls.append(href)


def _collect_l2_urls(l1, d, selectors: dict, urls: list) -> None:
    """Click l1, then collect URLs from all level-2 items."""
    try:
        l1.click()
        for l2 in d.select_all(selectors["menu_level2"], 2):
            _collect_l3_urls(l2, d, selectors, urls)
    except Exception:
        pass


# ─── Constantes JS (déclarées ici pour ne pas encombrer scrape_product) ──────

_IMAGES_JS = """
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
"""

_DOCS_JS = """
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
"""

_ECO_LABELS_JS = """
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
"""

_CONDITIONNEMENT_JS = """
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
"""

_CROSS_SELL_JS = """
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
"""


# ─── Helpers extraction page produit (non décorés → testables) ───────────────

def _extract_text(d, selector, clean_fn=None, timeout: int = 1) -> str:
    """Return element text via selector, optionally transformed; '' on failure."""
    try:
        text = d.select(selector, timeout).text
        return clean_fn(text) if clean_fn else text
    except Exception:
        return ""


def _extract_product_ref(d, selectors: dict) -> str:
    """Return the numeric product reference stripped of non-digits, or ''."""
    try:
        el = d.select(selectors["product_reference"], 1)
        if el:
            return re.sub(r"\D", "", el.text).strip()
    except Exception:
        pass
    return ""


def _extract_price(d, selectors: dict) -> str:
    """Try three CSS selectors in order to extract the numeric HT price."""
    price_el = None
    try:
        for _psel in [selectors["price_final"], "div.c-price.c-price--final", ".c-price--final"]:
            try:
                price_el = d.select(_psel, 1)
                if price_el:
                    break
            except Exception:
                continue
        if price_el:
            raw = price_el.text.replace("€", "").replace("\xa0", " ")
            m = re.search(r"(\d[\d ]*[.,]\d+|\d+)", raw)
            return m.group(1).replace(",", ".").replace(" ", "") if m else ""
    except Exception:
        pass
    return ""


def _extract_brand_img(d, selectors: dict) -> str:
    """Return brand logo src attribute, or '' on failure."""
    try:
        return d.select(selectors["brand_image"], 1).get_attribute("src") or ""
    except Exception:
        return ""


def _extract_brand_name(d, selectors: dict) -> str:
    """Return brand name from logo alt or title attribute, or ''."""
    try:
        el = d.select(selectors["brand_image"], 1)
        if el:
            return (el.get_attribute("alt") or el.get_attribute("title") or "").strip()
    except Exception:
        pass
    return ""


def _extract_js_list(d, js_code: str) -> List[str]:
    """Run JS and return the result as a list, or [] on failure."""
    try:
        result = d.run_js(js_code)
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _extract_js_scalar(d, js_code: str) -> str:
    """Run JS and return the result as a stripped string, or '' on failure."""
    try:
        return (d.run_js(js_code) or "").strip()
    except Exception:
        return ""


def _extract_attrs(d, selectors: dict) -> str:
    """Return product attributes as a '||'-joined 'Label=Value' string."""
    attrs: List[str] = []
    try:
        for char_row in d.select_all(selectors["characteristics_table"], 1):
            tds = char_row.select_all("td", 0)
            if len(tds) >= 2:
                label = " ".join(tds[0].text.split()).strip()
                value = " ".join(tds[1].text.split()).strip()
                if label and value:
                    attrs.append(f"{label}={value}")
    except Exception:
        pass
    return "||".join(attrs)


def _extract_ref_fab(d, selectors: dict, clean_fn) -> str:
    """Extract manufacturer ref from characteristics row, with fallback selector."""
    try:
        ref_row = d.select(selectors["characteristics_ref"], 1)
        if ref_row:
            tds = ref_row.select_all("td", 0)
            if tds:
                return clean_fn(tds[-1].text)
    except Exception:
        pass
    try:
        el = d.select(selectors["supplier_ref"], 1)
        if el:
            return clean_fn(el.text)
    except Exception:
        pass
    return ""


def _extract_ean(d, selectors: dict, clean_fn) -> str:
    """Return EAN barcode string, or '' on failure."""
    try:
        el = d.select(selectors["product_ean"], 1)
        if el:
            return clean_fn(el.text)
    except Exception:
        pass
    return ""


def _extract_cross_sell(d) -> List[str]:
    """Return cleaned cross-sell refs (numeric digits, _ suffix stripped)."""
    try:
        result = d.run_js(_CROSS_SELL_JS)
        refs = result if isinstance(result, list) else []
    except Exception:
        return []
    cleaned: List[str] = []
    for ref in refs:
        clean_ref = ref.split("_")[0] if "_" in ref else ref
        if clean_ref and clean_ref not in cleaned:
            cleaned.append(clean_ref)
    return cleaned


def _get_combo_headers(d, selectors: dict) -> List[str]:
    """Return combination table header texts, or [] on failure."""
    try:
        header_els = d.select_all(selectors["combinations_headers"], 1)
        return [h.text.strip() for h in header_els]
    except Exception:
        return []


def _set_child_refs(base_row: dict, combo_rows: list, clean_fn) -> None:
    """Collect all child refs from combo_rows and store in base_row['childRefs']."""
    all_child_refs = [base_row["productRef"]] if base_row["productRef"] else []
    for combo in combo_rows:
        tds = combo.select_all("td", 0)
        if tds:
            ref_td = clean_fn(tds[0].text)
            if ref_td and ref_td not in all_child_refs:
                all_child_refs.append(ref_td)
    base_row["childRefs"] = "||".join(all_child_refs)


def _build_combo_row(base_row: dict, combo, headers: list, child_refs_str: str, clean_fn) -> dict:
    """Build one combination row dict from a <tr> element."""
    row = dict(base_row)
    row["isCombination"]    = "True"
    row["combinationIndex"] = None
    row["parentRef"]        = base_row["productRef"]
    row["childRefs"]        = child_refs_str
    tds = combo.select_all("td", 0)
    if not tds:
        return row
    ref_td = clean_fn(tds[0].text)
    if ref_td:
        row["productRef"] = ref_td
    decli_parts: List[str] = []
    for h, td in zip(headers[1:], tds[1:]):
        val = td.text.replace("\xa0", " ").replace("\n", " ").replace("\r", "").replace("\t", " ")
        val = val.replace("AJOUTER", "").replace("Ajouter", "").replace("ajouter", "")
        val = " ".join(val.split()).strip()
        if not val:
            continue
        hdr = " ".join(h.strip().rstrip(":").split())
        decli_parts.append(f"{hdr}={val}")
    row["productDecliName&Value"] = "||".join(decli_parts)
    return row


def _build_rows(d, base_row: dict, selectors: dict, clean_fn) -> List[Dict]:
    """Return product rows: one base row, or one per combination."""
    try:
        combo_rows = d.select_all(selectors["combinations_table"], 1)
        if not combo_rows:
            base_row["isCombination"] = "False"
            base_row["combinationIndex"] = ""
            return [base_row]
        headers = _get_combo_headers(d, selectors)
        _set_child_refs(base_row, combo_rows, clean_fn)
        return [_build_combo_row(base_row, c, headers, base_row["childRefs"], clean_fn)
                for c in combo_rows]
    except Exception:
        base_row["isCombination"] = "False"
        base_row["combinationIndex"] = ""
        return [base_row]


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
            {"name": "CookiesConsent_ads",                     "value": "true", "url": URL_SITE},
            {"name": "CookiesConsent_individualCustomization",  "value": "true", "url": URL_SITE},
            {"name": "CookiesConsent_required",                "value": "1",    "url": URL_SITE},
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
            for l1 in d.select_all(SELECTORS["menu_level1"], 3):
                if category_filter and category_filter.lower() not in l1.text.lower():
                    continue
                _collect_l2_urls(l1, d, SELECTORS, urls)
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

    def scrape_product(self) -> List[Dict]:
        from css_selectors.legallais import SELECTORS
        from core.utils import clean_text
        d = self._driver
        product_ref = _extract_product_ref(d, SELECTORS)
        base_row = {
            "productRef":        product_ref,
            "productTitle":      _extract_text(d, SELECTORS["product_title"], clean_text),
            "productPrice":      _extract_price(d, SELECTORS),
            "price_original":    _extract_text(d, SELECTORS["price_original"], clean_text),
            "price_eco":         _extract_text(d, SELECTORS["price_eco"], clean_text),
            "stockStatus":       _extract_text(d, SELECTORS["stock"], clean_text),
            "productBrand":      _extract_brand_name(d, SELECTORS),
            "Image_Brand":       _extract_brand_img(d, SELECTORS),
            "productImages":     "||".join(_extract_js_list(d, _IMAGES_JS)),
            "productDesc":       _extract_text(d, SELECTORS["description"]),
            "productDocList":    "||".join(_extract_js_list(d, _DOCS_JS)),
            "ecoLabel":          "||".join(_extract_js_list(d, _ECO_LABELS_JS)),
            "conditionnement":   _extract_js_scalar(d, _CONDITIONNEMENT_JS),
            "productAttributes": _extract_attrs(d, SELECTORS),
            "Ref_fabricant":     _extract_ref_fab(d, SELECTORS, clean_text),
            "EAN":               _extract_ean(d, SELECTORS, clean_text),
            "ProductUrl":        d.current_url,
            "parentRef":         product_ref,
            "childRefs":         product_ref,
            "crossSell":         "||".join(_extract_cross_sell(d)),
        }
        return _build_rows(d, base_row, SELECTORS, clean_text)
