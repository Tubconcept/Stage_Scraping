"""
Moteur CSS du scraper Prolians (mode : products).
Ce fichier contient uniquement les appels CSS : sélecteurs, parsing,
extraction de données brutes depuis le HTML, et helpers de navigation/dates.
L'orchestration (run, boucle principale, persistance) se trouve dans scrap_prolians_products.py.
"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from selectors.prolians import Selectors
from core.config import CSV_HEADERS

today         = datetime.today().strftime("%Y-%m-%d")
BASE_URL      = Selectors.BASE_URL
SITEMAP_INDEX = Selectors.SITEMAP_INDEX

FIELDNAMES = CSV_HEADERS


# =============================
# SITEMAP
# =============================

def extract_sitemap_urls(session, url):
    r = session.get(url)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = []

    if root.tag.endswith("sitemapindex"):
        for sm in root.findall("ns:sitemap", ns):
            urls.append(sm.find("ns:loc", ns).text)
    else:
        for u in root.findall("ns:url", ns):
            urls.append(u.find("ns:loc", ns).text)

    return urls


# =============================
# LECTURE REFS + PRIX
# Lit Code P / Réf. fabricant / Réf. PROLIANS / prix / stock
# depuis l'état courant de la page (après chaque clic radio).
# =============================

def _read_refs_and_price(page):
    code_p = ref_fab = ref_prolians = price = stock = ""
    try:
        items = page.locator(Selectors.inline_list_item)
        for i in range(items.count()):
            try:
                text = items.nth(i).inner_text(timeout=3000).strip()
            except:
                continue
            m = re.search(r"Code P\s*[: ]\s*(\S+)", text)
            if m:
                code_p = m.group(1)
            m = re.search(r"Réf\.\s*fabricant\s*[: ]\s*(\S+)", text)
            if m:
                ref_fab = m.group(1)
            m = re.search(r"Réf\.\s*PROLIANS\s*[: ]\s*(\S+)", text)
            if m:
                ref_prolians = m.group(1)
    except:
        pass
    try:
        if page.locator(Selectors.price_message).count() > 0:
            stock = "Magasin seulement"
        else:
            raw = page.locator(Selectors.price).first.inner_text(timeout=2000)
            price = raw.replace("€", "").replace(",", ".").split()[0]
            stock = "Disponible"
    except:
        pass
    return code_p, ref_fab, ref_prolians, price, stock


# =============================
# DÉCLINAISONS
# =============================

def _extract_declinaisons(page, radios):
    """Clique chaque radio, récupère le label et la Réf. PROLIANS mise à jour."""
    declinaisons = []

    dim_name = ""
    try:
        rg = page.locator('[role="radiogroup"]').first
        dim_name = (rg.get_attribute("aria-label") or "").strip()
    except:
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
        except:
            pass
        if not variant_val:
            try:
                variant_val = (radio.get_attribute("aria-label") or "").strip()
            except:
                pass
        if not variant_val:
            try:
                variant_val = (radio.get_attribute("value") or "").strip()
            except:
                pass

        if dim_name and variant_val:
            if variant_val.startswith(dim_name):
                suffix = variant_val[len(dim_name):].strip().lstrip("-").strip()
                full_label = f"{dim_name} - {suffix}" if suffix else variant_val
            else:
                full_label = f"{dim_name} - {variant_val}"
        else:
            full_label = variant_val or f"Déclinaison {i+1}"

        try:
            radio.click(timeout=3000)
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"    Déclinaison {i+1} — erreur clic : {e}")
            continue

        code_p, ref_fab, ref_prolians, price, stock = _read_refs_and_price(page)

        print(f"    [{i+1}] {full_label}")
        print(f"          Réf. PROLIANS  : {ref_prolians or '—'}")
        print(f"          Réf. fabricant : {ref_fab or '—'}")
        print(f"          Prix           : {price or '—'} €")

        declinaisons.append({
            "label":        full_label,
            "code_p":       code_p,
            "ref_fab":      ref_fab,
            "ref_prolians": ref_prolians,
            "price":        price,
            "stock":        stock,
        })

    return declinaisons


# =============================
# DOM EXTRACTION
# =============================

def extract_product_from_dom(page):
    data = {k: "" for k in FIELDNAMES}

    # ---------------- REF
    try:
        page.wait_for_selector(Selectors.inline_list_item, timeout=5000)
    except:
        print("Pas trouvé, continuer")
        return None
    code_p_init, ref_fab_init, ref_prolians_init, price_init, stock_init = _read_refs_and_price(page)
    data["productRef"] = code_p_init

    # ---------------- BREADCRUMBS
    try:
        crumbs = page.locator(Selectors.breadcrumb)
        count = crumbs.count()
        cats = [crumbs.nth(i).inner_text() for i in range(2, min(count, 5))]
        if len(cats) > 0: data["cat1"] = cats[0]
        if len(cats) > 1: data["cat2"] = cats[1]
        if len(cats) > 2: data["cat3"] = cats[2]
        data["CategoryTree"] = ";".join(cats)
    except:
        pass

    # ---------------- TITLE
    try:
        data["productTitle"] = page.locator(Selectors.title).first.inner_text()
    except:
        pass

    # ---------------- CONDITIONNEMENT
    conditionnement = ""
    try:
        page.wait_for_selector(Selectors.conditionnement, timeout=5000)
        cond_text = page.locator(Selectors.conditionnement).first.inner_text().strip()
        m = re.search(r"(\d+)", cond_text)
        if m:
            conditionnement = m.group(1)
    except:
        pass

    data["conditionnement"] = conditionnement

    # ---------------- ATTRIBUTES
    try:
        attrs = []
        rows = page.locator(Selectors.attributes_row)
        for i in range(rows.count()):
            tds = rows.nth(i).locator("td")
            if tds.count() >= 2:
                attrs.append(f"{tds.nth(0).inner_text()}={tds.nth(1).inner_text()}")
        data["productAttributes"] = " , ".join(attrs)
    except:
        pass

    # ---------------- BRAND
    try:
        data["productBrand"] = page.locator(Selectors.brand_name).inner_text()
    except:
        pass

    try:
        data["Image_Brand"] = page.locator(Selectors.brand_image).first.get_attribute("src")
    except:
        pass

    # ---------------- DESCRIPTION
    try:
        btn = page.locator(Selectors.description_button)
        if btn.count() > 0:
            btn.first.click()
            page.wait_for_timeout(300)
    except:
        pass

    try:
        data["productDesc"] = page.locator(Selectors.description_content).inner_html()
    except:
        pass

    # ---------------- DOCUMENTS
    try:
        docs = page.locator(Selectors.documents)
        data["productDocList"] = ",".join(
            docs.nth(i).get_attribute("href") for i in range(docs.count())
        )
    except:
        pass

    # ---------------- IMAGES
    try:
        swiper_slides = page.locator(Selectors.image_swiper)
        if swiper_slides.count() > 0:
            srcs = []
            for i in range(swiper_slides.count()):
                img = swiper_slides.nth(i)
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                src = re.sub(r'width=\d+', 'width=600', src)
                if src and src not in srcs:
                    srcs.append(src)
            data["productImages"] = "||".join(srcs)
        else:
            imgs = page.locator(Selectors.image_fallback)
            srcs = []
            for i in range(imgs.count()):
                src = imgs.nth(i).get_attribute("src") or ""
                src = re.sub(r'width=\d+', 'width=600', src)
                if src and src not in srcs:
                    srcs.append(src)
            data["productImages"] = "||".join(srcs)
    except:
        pass

    # ---------------- COMBINATIONS / DÉCLINAISONS
    rows = []
    try:
        radios = page.locator(Selectors.combinations)
        if radios.count() > 0:
            # Produit avec variantes — prix/stock/refs lus par variante après chaque clic
            data["isCombination"] = "True"
            all_values = [radios.nth(i).get_attribute("value") for i in range(radios.count())]
            data["Parent"] = code_p_init
            data["Produit_liee"] = ",".join(v for v in all_values if v)

            declinaisons = _extract_declinaisons(page, radios)
            for idx, decli in enumerate(declinaisons, start=1):
                row = dict(data)
                row["combinationIndex"]       = idx
                row["productDecliName&Value"] = decli["label"]
                row["productRef"]             = decli["code_p"] or code_p_init
                row["Ref_fabricant"]          = decli["ref_fab"]
                row["Ref_Decli"]              = decli["ref_prolians"]
                row["productPrice"]           = decli["price"]
                row["stockStatus"]            = decli["stock"]
                row["ProductUrl"]             = page.url
                rows.append(row)
        else:
            # Produit simple — utilise les refs lues au chargement initial
            data["isCombination"]  = "False"
            data["Ref_fabricant"]  = ref_fab_init
            data["Ref_Decli"]      = ref_prolians_init
            data["productPrice"]   = price_init
            data["stockStatus"]    = stock_init
            data["ProductUrl"]     = page.url
            rows.append(data)
    except:
        data["ProductUrl"] = page.url
        rows.append(data)

    return rows
