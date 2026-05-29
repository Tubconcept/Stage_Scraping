import csv
import os
import sys
import re
import argparse
import requests
from datetime import datetime
import xml.etree.ElementTree as ET
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from selectors.prolians import Selectors

# =============================
# CONFIG
# =============================
today = datetime.today().strftime("%Y-%m-%d")

BASE_URL = Selectors.BASE_URL
SITEMAP_INDEX = Selectors.SITEMAP_INDEX
crash_file = "log/crash_products.txt"

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")

USERNAME = os.getenv("User")
PASSWORD = os.getenv("Password")

LIMIT_TEST = None  # ex: 10 pour tester

# =============================
# CSV STRUCTURE
# =============================
FIELDNAMES = [
    "productRef", "EAN", "Ref_fabicant", "cat1", "cat2", "cat3",
    "productTitle", "conditionnement", "productAttributes", "productBrand",
    "Image Brand", "productDesc", "productDocList", "productImages",
    "combinationIndex", "productCombinationNames", "productCombinationValues",
    "isCombination", "Parent", "productPrice", "Eco_Tax", "Reduction",
    "Produit liée", "stockStatus", "CategoryTree"
]

# =============================
# LOGIN FUNCTION
# =============================

def login(page):
    page.goto(Selectors.LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1000)

    if not page.url.startswith(Selectors.LOGIN_URL):
        print("Redirection inattendue après goto /login :", page.url)
        return False

    # étape 1 - email
    try:
        email_input = page.locator(Selectors.email_input)
        email_input.wait_for(state="visible", timeout=8000)
        email_input.fill(USERNAME)
        page.locator(Selectors.email_button).click()
    except Exception as e:
        print(f"étape email échouée : {e}")
        return False

    # étape 2 - mot de passe
    try:
        pwd_input = page.locator(Selectors.password_input)
        pwd_input.wait_for(state="visible", timeout=8000)
        pwd_input.fill(PASSWORD)
        page.locator(Selectors.submit_button).click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception as e:
        print(f"étape mot de passe échouée : {e}")
        return False

    # Vérification post-login
    page.wait_for_timeout(1500)
    if page.url.startswith(Selectors.LOGIN_URL):
        print("Login échoué - toujours sur la page de connexion")
        return False

    print("Login OK")
    return True

# =============================
# IS LOGGED IN FUNCTION
# =============================

def is_logged_in(page):
    try:
        if page.locator(Selectors.logged_in_check).count() > 0:
            print("Déjà connecté")
            return True
        elif page.locator(Selectors.logged_out_check).count() > 0:
            print(" Non connecté ou déconnecté")
            return False
        else:
            print("État de connexion inconnu")
            return False
    except:
        print("État de connexion inconnu (exception)")
        return False

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
# DOM EXTRACTION
# =============================
def extract_product_from_dom(page, combi_counters):
    data = {k: "" for k in FIELDNAMES}

    # ---------------- REF
    try:
        page.wait_for_selector(Selectors.inline_list_item, timeout=5000)
    except:
        print("Pas trouvé, continuer")
        return None
    refs = page.locator(Selectors.inline_list_item)
    product_ref = fabricant_ref = ""
    for i in range(refs.count()):
        try:
            text = refs.nth(i).inner_text(timeout=5000).strip()
            m1 = re.search(r"Réf\. PROLIANS\s*[: ]\s*(\S+)", text)
            m2 = re.search(r"Réf\. fabricant\s*[: ]\s*(\S+)", text)
            m3 = re.search(r"Code P\s*[: ]\s*(\S+)", text)
            if m1:
                product_ref = m1.group(1)
            if m2:
                fabricant_ref = m2.group(1)
            if m3:
                product_ref = m3.group(1)
        except:
            print(" Erreur lors de l'extraction des références ")

    data["productRef"] = product_ref
    data["Ref_fabicant"] = fabricant_ref

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
        data["Image Brand"] = page.locator(Selectors.brand_image).first.get_attribute("src")
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

    # ---------------- PRICE / STOCK
    try:
        if page.locator(Selectors.price_message).count() > 0:
            data["stockStatus"] = "Magasin seulement"
        else:
            try:
                price = page.locator(Selectors.price).first.inner_text()
                data["productPrice"] = price.replace("€", "").replace(",", ".").split()[0]
                data["stockStatus"] = "Disponible"
            except:
                pass
    except:
        pass

    # ---------------- COMBINATIONS
    try:
        radios = page.locator(Selectors.combinations)
        if radios.count() > 0:
            data["isCombination"] = "True"
            values = [radios.nth(i).get_attribute("value") for i in range(radios.count())]
            parent = values[0]
            data["Parent"] = parent

            if parent not in combi_counters:
                combi_counters[parent] = 0
            combi_counters[parent] += 1
            data["combinationIndex"] = combi_counters[parent]

            data["Produit liée"] = ",".join(v for v in values if v != data["productRef"])
        else:
            data["isCombination"] = "False"
    except:
        pass

    return data


# =============================
# MAIN
# =============================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", type=int, default=1, help="Numéro de la partie (1-based)")
    parser.add_argument("--total", type=int, default=1, help="Nombre total de parties")
    args = parser.parse_args()

    if args.part < 1 or args.part > args.total:
        print(f" --part doit etre entre 1 et --total ({args.total})")
        sys.exit(1)

    suffix = f"_part{args.part}of{args.total}" if args.total > 1 else ""
    csv_file = f"csv/scrap_p3_PW_{today}{suffix}.csv"
    crash_file = f"log/crash_products{suffix}.txt"

    count_produit = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.set_default_timeout(5000)
        context.set_default_navigation_timeout(10000)
        page = context.new_page()

        # -------- LOGIN
        page.goto(BASE_URL)
        try:
            page.locator(Selectors.cookie_acceptt).click()
        except:
            pass

        if not login(page):
            print(" Impossible de se connecter - arrét.")
            browser.close()
            sys.exit(1)
        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_timeout(1000)

        # -------- REQUEST SESSION
        session = requests.Session()
        for c in context.cookies():
            session.cookies.set(c["name"], c["value"], domain=".prolians.fr")

        # -------- SITEMAPS
        sitemap_files = extract_sitemap_urls(session, SITEMAP_INDEX)
        product_sitemap = next(u for u in sitemap_files if "product" in u)
        all_product_files = extract_sitemap_urls(session, product_sitemap)

        # Répartition par fichiers sitemap
        chunk = len(all_product_files) // args.total
        start = (args.part - 1) * chunk
        end = start + chunk if args.part < args.total else len(all_product_files)
        product_files = all_product_files[start:end]
        print(f" Partie {args.part}/{args.total} — {len(product_files)} fichiers sitemap ({start}—{end-1})")

        product_urls = []
        for f in product_files:
            product_urls.extend(extract_sitemap_urls(session, f))

        if LIMIT_TEST:
            product_urls = product_urls[:LIMIT_TEST]

        print(f" Produits détectés : {len(product_urls)}")

        # -------- CSV
        combi_counters = {}

        start_url = input("URL produit de départ (Entrée pour début) : ").strip()
        skip_mode = bool(start_url)

        if skip_mode:
            try:
                start_index = product_urls.index(start_url)
                product_urls = product_urls[start_index:]
                print(f" Reprise à l'index {start_index}")
            except ValueError:
                print("URL non trouvée dans la sitemap")
                sys.exit(1)

        file_exists = os.path.isfile(csv_file)

        with open(csv_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, FIELDNAMES, delimiter=";", quoting=csv.QUOTE_ALL)
            if not file_exists:
                writer.writeheader()

            for url in product_urls:
                max_retries = 3
                loaded = False
                for attempt in range(max_retries):
                    try:
                        page.goto(url, wait_until="load", timeout=5000)
                        page.wait_for_selector(Selectors.title, timeout=5000)
                        loaded = True
                        break
                    except:
                        print(f"Tentative {attempt+1}/{max_retries} échouée : {url}")

                if not loaded:
                    print(f" Impossible de charger {url}")
                    with open(crash_file, "a", encoding="utf-8") as cf:
                        cf.write(url + "\n")
                    continue

                if count_produit % 20 == 0 and not is_logged_in(page):
                    if not login(page):
                        print(" Re-login échoué — arrêt.")
                        browser.close()
                        sys.exit(1)
                    page.goto(url, wait_until="domcontentloaded", timeout=10000)

                data = extract_product_from_dom(page, combi_counters)
                if not data:
                    print("Produit ignoré (data=None)")
                    with open(crash_file, "a", encoding="utf-8") as cf:
                        cf.write(url + "\n")
                    continue
                writer.writerow(data)
                f.flush()
                count_produit += 1
                print(f"[{count_produit}] {data['productRef']}")

        browser.close()
        print(f"\n CSV généré : {csv_file}")


if __name__ == "__main__":
    main()
