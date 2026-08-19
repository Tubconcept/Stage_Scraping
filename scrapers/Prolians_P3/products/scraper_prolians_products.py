"""
Moteur d'extraction DOM Prolians (mode : produits).

Responsabilités :
- Téléchargement et parsing des sitemaps XML (index + URLs produit) ;
- Lecture des références, prix, stock, EAN, éco-participation sur la fiche ;
- Gestion des déclinaisons (boutons radio) : une ligne de données par variante ;
- Construction des dictionnaires alignés sur ``CSV_HEADERS`` / schéma SQLite.

L'orchestration Playwright, login et persistance sont dans ``scrap_prolians_products.py``.
"""
import sys
from pathlib import Path

# --- Racine projet ---
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from css_selectors.prolians import Selectors
from core.config import CSV_HEADERS
from core.logger import setup_logger
from core.utils import normalize_price

log   = setup_logger("prolians.products")
today = datetime.today().strftime("%Y-%m-%d")
BASE_URL      = Selectors.BASE_URL
SITEMAP_INDEX = Selectors.SITEMAP_INDEX

FIELDNAMES = CSV_HEADERS


# =============================
# SITEMAP
# =============================

def extract_sitemap_urls(session, url):
    """
    Récupère les URLs enfants d'un sitemap Prolians.

    - ``sitemapindex`` : liste de sous-fichiers .xml ;
    - ``urlset``       : liste d'URLs produit finales.
    """
    r = session.get(url)
    r.raise_for_status()
    root = ET.fromstring(r.content)

    # Détection automatique du namespace (http:// ou https://, selon le sitemap réel)
    ns_match = re.match(r'\{([^}]+)\}', root.tag)
    ns_uri = ns_match.group(1) if ns_match else "http://www.sitemaps.org/schemas/sitemap/0.9"
    ns = {"ns": ns_uri}

    urls = []

    if root.tag.endswith("sitemapindex"):
        for sm in root.findall("ns:sitemap", ns):
            loc = sm.find("ns:loc", ns)
            if loc is not None:
                urls.append(loc.text)
    else:
        for u in root.findall("ns:url", ns):
            loc = u.find("ns:loc", ns)
            if loc is not None:
                urls.append(loc.text)

    return urls


# =============================
# LECTURE REFS + PRIX + EAN + ECO_TAX + REDUCTION
# Lit Code P / Réf. fabricant / Réf. PROLIANS / prix / stock / EAN / Eco_Tax / Réduction
# depuis l'état courant de la page (après chaque clic radio).
# =============================

def _read_refs_and_price(page):
    """
    Lit l'état courant de la fiche (après clic sur une déclinaison éventuelle).

    Les références sont souvent regroupées dans des ``inline_list_item`` ;
    le prix et le stock dépendent des sélecteurs ``price`` / ``price_message``.
    """
    code_p = ref_fab = ref_prolians = price = stock = ean = eco_tax = reduction = ""
    try:
        # Agrège le texte de tous les blocs « liste inline » pour les regex
        items = page.locator(Selectors.inline_list_item)
        full_text = ""
        for i in range(items.count()):
            try:
                text = items.nth(i).inner_text(timeout=3000).strip()
                full_text += " " + text
            except Exception:
                continue

        # Parse les références
        # Séparateur après « Code P » = U+202F (espace fine insécable) sur le site
        # → ``\s*`` (qui couvre  ), pas ``[ :]`` qui le ratait (réf vide).
        m = re.search(r"Code P\s*[: ]?\s*(\S+)", full_text)
        if m:
            code_p = m.group(1)
        m = re.search(r"Réf\.\s*fabricant\s*[: ]\s*(\S+)", full_text)
        if m:
            ref_fab = m.group(1)
        m = re.search(r"Réf\.\s*PROLIANS\s*[: ]\s*(\S+)", full_text)
        if m:
            ref_prolians = m.group(1)
        m = re.search(r"EAN\s*[: ]\s*(\d{8,14})", full_text, re.IGNORECASE)
        if m:
            ean = m.group(1)
    except Exception:
        pass
    try:
        try:
            page.wait_for_selector(Selectors.price, timeout=3000)
        except Exception:
            pass

        # Message « prix sur demande » ou indisponible → pas de prix affiché
        if page.locator(Selectors.price_message).count() > 0:
            stock = "non disponible"
        else:
            elems = page.locator(Selectors.price)
            if elems.count() > 0:
                raw = elems.first.inner_text(timeout=2000)
                if raw and "€" in raw:
                    price = normalize_price(raw)
                    stock = "disponible"
            if not stock:
                stock = "non disponible"
    except Exception:
        pass
    try:
        eco_elem = page.locator(Selectors.eco_tax)
        if eco_elem.count() > 0:
            raw_eco = eco_elem.first.inner_text(timeout=2000)
            m = re.search(r"([\d,]+)\s*€", raw_eco)
            if m:
                eco_tax = m.group(1).replace(",", ".")
    except Exception:
        pass
    try:
        red_elem = page.locator(Selectors.reduction)
        if red_elem.count() > 0:
            raw_red = red_elem.first.inner_text(timeout=2000)
            m = re.search(r"[-−]?\s*([\d,]+)\s*%", raw_red)
            if m:
                reduction = m.group(1).replace(",", ".")
    except Exception:
        pass
    return code_p, ref_fab, ref_prolians, price, stock, ean, eco_tax, reduction


# =============================
# DÉCLINAISONS
# =============================

def _select_variant(page, radio) -> None:
    """Sélectionne une variante et attend le re-render de sa réf PROLIANS.

    L'``<input>`` radio (React Aria) est masqué/intercepté par son habillage CSS
    → un clic natif tombe en timeout ; ``force=True`` dispatche l'événement
    directement. La réf PROLIANS n'apparaît qu'APRÈS le re-render (la fiche
    n'affiche que « Code P » tant qu'aucune variante n'est choisie), d'où l'attente.
    """
    radio.click(timeout=3000, force=True)
    try:
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll(\"div[data-testid='inline-list-item']\"))"
            ".some(e => e.innerText.includes('PROLIANS'))",
            timeout=4000,
        )
    except Exception:
        pass
    page.wait_for_timeout(400)


def _extract_declinaisons(page, radios):
    """Clique chaque radio, récupère le label et toutes les données mises à jour."""
    declinaisons = []

    dim_name = ""
    try:
        rg = page.locator('[role="radiogroup"]').first
        dim_name = (rg.get_attribute("aria-label") or "").strip()
    except Exception:
        pass

    for i in range(radios.count()):
        radio = radios.nth(i)

        variant_val = ""
        try:
            rid = radio.get_attribute("id") or ""
            if rid:
                lbl = page.locator(f"label[for='{rid}']")
                if lbl.count() > 0:
                    variant_val = lbl.inner_text(timeout=3000).strip()
        except Exception:
            pass
        if not variant_val:
            try:
                variant_val = (radio.get_attribute("aria-label") or "").strip()
            except Exception:
                pass
        if not variant_val:
            try:
                variant_val = (radio.get_attribute("value") or "").strip()
            except Exception:
                pass

        # Format : "Longueur totale : 50mm" (séparateur " : ")
        if dim_name and variant_val:
            if variant_val.startswith(dim_name):
                suffix = variant_val[len(dim_name):].strip().lstrip(":").lstrip("-").strip()
                full_label = f"{dim_name} : {suffix}" if suffix else variant_val
            else:
                full_label = f"{dim_name} : {variant_val}"
        else:
            full_label = variant_val or f"Déclinaison {i+1}"

        try:
            _select_variant(page, radio)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log.warning(f"Déclinaison {i+1} — erreur clic : {e}")
            continue

        vals = _read_refs_and_price(page)
        if not vals[2]:  # ref_prolians vide
            # Le 1er clic est parfois absorbé par un overlay résiduel de la fiche
            # (tiroir description fermé juste avant) → on resélectionne et on relit.
            try:
                _select_variant(page, radio)
                vals = _read_refs_and_price(page)
            except Exception:
                pass
        code_p, ref_fab, ref_prolians, price, stock, ean, eco_tax, reduction = vals

        log.debug(f"[{i+1}] {full_label} | Code P: {code_p or '—'} | Réf. fab: {ref_fab or '—'} | Prix: {price or '—'} €")

        declinaisons.append({
            "label":        full_label,
            "code_p":       code_p,
            "ref_fab":      ref_fab,
            "ref_prolians": ref_prolians,
            "price":        price,
            "stock":        stock,
            "ean":          ean,
            "eco_tax":      eco_tax,
            "reduction":    reduction,
        })

    return declinaisons


# =============================
# DOM EXTRACTION
# =============================

def extract_product_from_dom(page):
    """
    Extrait une ou plusieurs lignes produit depuis la page Playwright courante.

    Retourne une liste de dicts (une entrée par déclinaison) ou ``None`` si la
    fiche est illisible (sélecteur références absent).
    """
    data = dict.fromkeys(FIELDNAMES, "")
   

    # --------------- FOURNISSEUR (identifiant interne Tubconcept)
    data["product_fournisseur"] = "P3"

    # ---------------- REF
    try:
        page.wait_for_selector(Selectors.inline_list_item, timeout=5000)
    except Exception:
        log.warning("Sélecteur refs introuvable — produit ignoré")
        return None
    # Le bloc existe avant d'être peuplé : son texte (Code P / Réf / EAN) arrive au
    # 2ᵉ rendu React. On attend qu'il soit NON VIDE (sinon réf/Code P lus vides sur
    # les fiches qui rendent un peu plus lentement) — NON bloquant : on lit ensuite
    # ce qui est présent.
    try:
        page.wait_for_function(
            "() => { const e = document.querySelector(\"div[data-testid='inline-list-item']\");"
            " return !!e && e.innerText.trim().length > 0; }",
            timeout=4000,
        )
    except Exception:
        pass

    code_p_init, ref_fab_init, ref_prolians_init, price_init, stock_init, ean_init, eco_tax_init, reduction_init = _read_refs_and_price(page)
    # Réf de repli : ~28 % des fiches n'exposent que le « Code P » (= token d'URL,
    # unique), sans « Réf. PROLIANS » → on garantit une référence exploitable.
    ref_init = ref_prolians_init or code_p_init
    data["product_reference_fournisseur"] = ref_init
    data["product_ean"]                   = ean_init
    data["product_eco_taxe"]              = eco_tax_init
    data["product_promotion"]             = reduction_init

    # ---------------- BREADCRUMBS
    try:
        crumbs = page.locator(Selectors.breadcrumb)
        count = crumbs.count()
        # Ignore accueil + 1er niveau ; garde jusqu'à 3 catégories feuilles
        cats = [crumbs.nth(i).inner_text() for i in range(2, min(count, 5))]
        data["product_category_tree"] = "||".join(cats)
    except Exception:
        pass

    # ---------------- TITLE
    try:
        data["product_designation"] = page.locator(Selectors.title).first.inner_text()
    except Exception:
        pass

    # ---------------- CONDITIONNEMENT
    try:
        page.wait_for_selector(Selectors.conditionnement, timeout=5000)
        cond_text = page.locator(Selectors.conditionnement).first.inner_text().strip()
        m = re.search(r"(\d+)", cond_text)
        if m:
            data["product_conditionnement"] = m.group(1)
    except Exception:
        pass

    # ---------------- ATTRIBUTES
    try:
        attrs = []
        rows = page.locator(Selectors.attributes_row)
        for i in range(rows.count()):
            tds = rows.nth(i).locator("td")
            if tds.count() >= 2:
                attrs.append(f"{tds.nth(0).inner_text()}:{tds.nth(1).inner_text()}")
        data["product_attributes"] = "||".join(attrs)
    except Exception:
        pass

    # ---------------- BRAND
    try:
        data["product_brand"] = page.locator(Selectors.brand_name).inner_text()
    except Exception:
        pass
    try:
        data["product_brand_logo_url"] = page.locator(Selectors.brand_image).first.get_attribute("src")
    except Exception:
        pass

    # ---------------- DESCRIPTION
    try:
        btn = page.locator(Selectors.description_button)
        if btn.count() > 0:
            btn.first.click()
            page.wait_for_timeout(300)
    except Exception:
        pass
    try:
        data["product_description"] = page.locator(Selectors.description_content).inner_html()
    except Exception:
        pass
    # Ferme le drawer description (drawer React Aria fixe) pour débloquer les clics suivants
    try:
        if page.locator('[data-rac][class*="z-drawer"]').count() > 0:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
    except Exception:
        pass

    # ---------------- DOCUMENTS
    try:
        docs = page.locator(Selectors.documents)
        doc_urls = [docs.nth(i).get_attribute("href") for i in range(docs.count())]
        data["product_docs_url"] = "||".join(u for u in doc_urls if u)
    except Exception:
        pass

    # ---------------- IMAGES
    try:
        swiper_slides = page.locator(Selectors.image_swiper)
        if swiper_slides.count() > 0:
            srcs = []
            for i in range(swiper_slides.count()):
                img = swiper_slides.nth(i)
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                # Normalise la résolution miniatures pour URLs stables
                src = re.sub(r'width=\d+', 'width=600', src)
                if src and src not in srcs:
                    srcs.append(src)
            data["product_image_url"] = "||".join(srcs)
        else:
            imgs = page.locator(Selectors.image_fallback)
            srcs = []
            for i in range(imgs.count()):
                src = imgs.nth(i).get_attribute("src") or ""
                src = re.sub(r'width=\d+', 'width=600', src)
                if src and src not in srcs:
                    srcs.append(src)
            data["product_image_url"] = "||".join(srcs)
    except Exception:
        pass

    # ---------------- CROSS-SELL (produits similaires)
    # HTML : <span data-testid="product-card/reference">Ref. P70JK9P</span>
    try:
        cross_refs = page.locator(Selectors.cross_sell_ref)
        refs = []
        for i in range(cross_refs.count()):
            txt = cross_refs.nth(i).inner_text().strip()
            m = re.search(r'R[eé]f\.?\s*(\S+)', txt)
            if m:
                ref = m.group(1)
                if ref and ref != data.get("product_reference_fournisseur") and ref not in refs:
                    refs.append(ref)
        if refs:
            data["product_cross_sell"] = "||".join(refs)
    except Exception:
        pass

    # ---------------- ECO-LABELS
    try:
        eco_labels = page.locator(Selectors.eco_labels)
        labels = set()
        for i in range(eco_labels.count()):
            alt = eco_labels.nth(i).get_attribute("alt") or ""
            title = eco_labels.nth(i).get_attribute("title") or ""
            label = alt or title
            if label and "eco" in label.lower():
                labels.add(label.strip())
        if labels:
            data["product_eco_label"] = "||".join(sorted(labels))
    except Exception:
        pass

    # ── Collecte toutes les réfs PROLIANS visibles sur la page ──────────────────
    # Le tableau de déclinaisons en bas affiche toutes les refs sans clic radio.
    # re.findall capture la liste complète dans l'ordre du document.
    _inline_text = ""
    try:
        _items = page.locator(Selectors.inline_list_item)
        for _j in range(_items.count()):
            try:
                _inline_text += " " + _items.nth(_j).inner_text(timeout=3000).strip()
            except Exception:
                pass
    except Exception:
        pass

    _seen_pr: set = set()
    _all_prolians_refs: list = []
    for _m in re.finditer(r"Réf\.\s*PROLIANS\s*[: ]\s*(\S+)", _inline_text):
        _r = _m.group(1)
        if _r not in _seen_pr:
            _seen_pr.add(_r)
            _all_prolians_refs.append(_r)

    # ---------------- COMBINATIONS / DÉCLINAISONS
    rows = []
    try:
        radios   = page.locator(Selectors.combinations)
        has_radio = radios.count() > 0
        # Produit combiné si boutons radio détectés OU plusieurs refs PROLIANS en page
        is_combi  = has_radio or len(_all_prolians_refs) > 1

        if is_combi:
            data["products_is_combination"] = "True"
            # Parent = première ref dans l'ordre du document
            data["product_parent_reference"] = (
                _all_prolians_refs[0] if _all_prolians_refs else ref_init
            )
            # child_refs = toutes les refs trouvées sur la page
            full_child_refs = (
                "||".join(_all_prolians_refs) if _all_prolians_refs else ref_init
            )

            if has_radio:
                declinaisons = _extract_declinaisons(page, radios)
                # Sur ces fiches la réf PROLIANS n'existe qu'APRÈS sélection d'une
                # variante (init ne montre que « Code P ») → parent/child collectés
                # avant clic sont vides : on les reconstruit depuis les variantes.
                _decli_refs = [d["ref_prolians"] or d.get("code_p", "")
                               for d in declinaisons
                               if (d.get("ref_prolians") or d.get("code_p"))]
                if _decli_refs:
                    if not data["product_parent_reference"]:
                        data["product_parent_reference"] = _decli_refs[0]
                    full_child_refs = "||".join(
                        dict.fromkeys([
                            data["product_parent_reference"],
                            *_decli_refs,
                        ])
                    )
            else:
                # Pas de bouton radio → construit les déclinaisons depuis les refs de page
                declinaisons = [
                    {
                        "label":        r,
                        "code_p":       r,
                        "ref_fab":      ref_fab_init,
                        "ref_prolians": r,
                        "price":        price_init,
                        "stock":        stock_init,
                        "ean":          ean_init,
                        "eco_tax":      eco_tax_init,
                        "reduction":    reduction_init,
                    }
                    for r in _all_prolians_refs
                ]

                full_child_refs = "||".join(
                    dict.fromkeys([
                        data["product_parent_reference"],
                        *_all_prolians_refs,
                    ])
                )

            for decli in declinaisons:
                row = dict(data)
                row["product_combination_index"]     = None
                row["product_combination_values"]    = decli["label"]
                row["product_reference_fournisseur"] = (
                    decli["ref_prolians"] or decli.get("code_p") or ref_init
                )
                row["product_reference_fabricant"]   = decli["ref_fab"] or ref_fab_init
                row["product_ean"]                   = decli["ean"] or ean_init
                row["product_eco_taxe"]              = decli["eco_tax"] or eco_tax_init
                row["product_promotion"]             = decli["reduction"] or reduction_init
                row["product_child_reference"]       = full_child_refs
                row["product_price_ht"]              = decli["price"]
                row["product_stock_status"]          = decli["stock"]
                row["product_fournisseur_url"]       = page.url
                rows.append(row)
        else:
            # Produit simple — parent = enfant = la même référence PROLIANS
            data["products_is_combination"]     = "False"
            data["product_reference_fabricant"] = ref_fab_init
            data["product_parent_reference"]    = ref_init
            data["product_child_reference"]     = ref_init
            data["product_combination_index"]   = ""
            data["product_combination_values"]  = data.get("product_designation", "Produit standard")
            data["product_price_ht"]            = price_init
            data["product_stock_status"]        = stock_init
            data["product_fournisseur_url"]     = page.url
            rows.append(data)
    except Exception:
        data["product_fournisseur_url"] = page.url
        rows.append(data)

    return rows
