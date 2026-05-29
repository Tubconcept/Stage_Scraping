"""
Moteur CSS du scraper Prolians (mode : orders).
Ce fichier contient uniquement les appels CSS : sélecteurs, parsing,
extraction de données brutes depuis le HTML, et helpers de navigation/dates.
L'orchestration (run, boucle principale, persistance) se trouve dans scrap_prolians_orders.py.
"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import re
import os
import traceback
from datetime import datetime
from css_selectors.prolians import Selectors
from core.utils import clean_text

BASE_URL = Selectors.BASE_URL
today    = datetime.today().strftime('%Y-%m-%d')


# =============================
# LOG
# =============================

def log_exception(today, e, commentaire=""):
    ignorer = [
        "Target page, context or browser has been closed",
        "Browser has been closed",
        "TargetClosedError"
    ]
    if any(msg in str(e) for msg in ignorer):
        return

    os.makedirs("log", exist_ok=True)
    with open(f"log/logException-{today}-CMD.txt", "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime('[%Y-%m-%d %H:%M:%S]')
        f.write(f"{timestamp} {type(e).__name__}: {str(e)}\n")
        tb = traceback.extract_tb(e.__traceback__)
        for frame in tb:
            f.write(f"  File \"{frame.filename}\", line {frame.lineno}, in {frame.name}\n")
            f.write(f"    {frame.line}\n")
        if commentaire:
            f.write(f"Commentaire: {commentaire}\n")
        f.write("\n")


# =============================
# NAVIGATION COMMANDES
# =============================

def navigate_to_orders(page):
    account_url = f"{BASE_URL}/customer/account"
    page.goto(account_url, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    try:
        page.wait_for_selector(Selectors.view_all_orders, timeout=8000)
        page.locator(Selectors.view_all_orders).click()
        page.wait_for_timeout(4000)
    except Exception as e:
        print(f"Clic 'Voir toutes mes commandes' échoué : {e} — navigation directe")
        page.goto(f"{BASE_URL}/customer/account/history/orders/web", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

    try:
        page.wait_for_selector(Selectors.order_row, timeout=20000)
        print("Page commandes chargée")
    except:
        print("Sélecteur order_row introuvable sur cette page")


def collect_orders(page, date_inf, date_sup):
    orders = []
    seen = set()

    while True:
        rows = page.locator(Selectors.order_row).all()
        if not rows:
            break

        stop = False
        for row in rows:
            try:
                webref = row.locator(Selectors.order_webref).inner_text(timeout=3000).strip()
                internalref_el = row.locator(Selectors.order_internalref)
                internalref = internalref_el.inner_text(timeout=3000).strip() if internalref_el.count() > 0 else ""
                date_txt = row.locator(Selectors.order_date).inner_text(timeout=3000).strip()
                date_cmd = datetime.strptime(date_txt, "%d/%m/%Y")
                status_el = row.locator(Selectors.order_status)
                status_txt = status_el.inner_text(timeout=3000).strip() if status_el.count() > 0 else ""
            except Exception as e:
                print(f"Erreur lecture ligne commande : {e}")
                continue

            if date_cmd > date_sup:
                continue

            if date_cmd < date_inf:
                stop = True
                break

            if webref not in seen:
                seen.add(webref)
                orders.append({
                    "webref": webref,
                    "internalref": internalref,
                    "date": date_cmd.strftime("%d/%m/%Y"),
                    "status": status_txt,
                })
                print(f"  {webref} ({internalref}) - {date_cmd.date()}")

        if stop:
            break

        next_btn = page.locator(Selectors.next_page)
        if next_btn.count() == 0:
            print("Dernière page atteinte")
            break
        if next_btn.get_attribute("disabled") is not None:
            print("Dernière page atteinte")
            break

        first_webref = page.locator(Selectors.order_webref).first.inner_text(timeout=3000).strip()
        next_btn.click()
        page.wait_for_timeout(2000)
        for _ in range(10):
            try:
                new_first = page.locator(Selectors.order_webref).first.inner_text(timeout=1000).strip()
                if new_first != first_webref:
                    break
            except:
                pass
            page.wait_for_timeout(500)

    return orders


# =============================
# DÉTAIL COMMANDE
# =============================

def get_info(page, order):
    webref = order["webref"]
    detail_url = f"{BASE_URL}/customer/account/history/orders/web/{webref}"

    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
    except Exception as e:
        log_exception(today, e, f"Navigation {detail_url}")
        return None

    # Référence commande client
    try:
        ref_cmd = page.locator(f"xpath={Selectors.client_order_ref_xpath}").inner_text(timeout=5000).strip()
        ref_cmd = clean_text(ref_cmd)
    except:
        ref_cmd = ""

    # Produits (plusieurs lignes possibles)
    prdt_data = []
    try:
        names_locs = page.locator(Selectors.product_name).all()
        refs_locs  = page.locator(f"xpath={Selectors.product_ref_xpath}").all()
        qtys_locs  = page.locator(f"xpath={Selectors.prodcut_qty_xpath}").all()

        for i in range(len(names_locs)):
            try:
                name = clean_text(names_locs[i].inner_text(timeout=3000)) if i < len(names_locs) else ""

                ref_txt = refs_locs[i].inner_text(timeout=3000).strip() if i < len(refs_locs) else ""
                m_ref = re.search(r"Réf\. PROLIANS\s*[: ]\s*(\S+)", ref_txt)
                ref_prdt = m_ref.group(1) if m_ref else ""

                qty_txt = qtys_locs[i].inner_text(timeout=3000).strip() if i < len(qtys_locs) else ""
                m_qty = re.search(r"Qt\s*:\s*(\d+)", qty_txt)
                prdt_qty = m_qty.group(1) if m_qty else ""

                prdt_data.append(f"{ref_prdt}:{name}:{prdt_qty}:")
            except Exception as e:
                log_exception(today, e, f"Produit ligne {i} de {webref}")
    except Exception as e:
        log_exception(today, e, f"Extraction produits {webref}")

    print(prdt_data)

    return {
        "ref_px":     order.get("internalref", ""),
        "ref_cmd":    ref_cmd,
        "date_cmd":   order.get("date", ""),
        "statut_cmd": order.get("status", ""),
        "prdt_data":  ",".join(prdt_data),
    }
