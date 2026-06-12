"""
Moteur d'extraction DOM Prolians (mode : commandes).

- Navigation vers l'historique des commandes web ;
- Parcours paginé du tableau avec filtre par plage de dates ;
- Ouverture de chaque fiche détail pour récupérer réf. client et lignes produits.

Format ``prdt_data`` : ``ref_prolians:nom:qté:`` concaténés par des virgules.
L'orchestration et SQLite sont dans ``scrap_prolians_orders.py``.
"""
import sys
from pathlib import Path

# --- Racine projet ---
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
    """Journalise une exception dans log/logException-{date}-CMD.txt (hors fermeture navigateur)."""
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
    """Accède au compte client puis à la liste complète des commandes web."""
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
    """
    Parcourt toutes les pages du tableau commandes dans l'ordre anti-chronologique.

    Dès qu'une commande « dans la plage » a été vue, toute ligne ultérieure hors
    plage déclenche l'arrêt (``started`` + ``stop``) : hypothèse de tri par date décroissante.
    """
    orders = []
    seen = set()
    inf = date_inf.date() if hasattr(date_inf, "date") else date_inf
    sup = date_sup.date() if hasattr(date_sup, "date") else date_sup
    started = False  # passe à True dès la première ligne retenue dans l'intervalle

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

            row_date = date_cmd.date()
            if not (inf <= row_date <= sup):
                # On a dépassé la fenêtre après avoir collecté → fin de pagination
                if started:
                    stop = True
                    break
                continue

            if webref not in seen:
                started = True
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

        # Attend que la première ligne change après clic « page suivante »
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
    """Charge la page détail d'une commande et assemble le dict persisté."""
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

        # XPath original → fallback sur tout élément si aucun ne correspond
        refs_locs = page.locator(f"xpath={Selectors.product_ref_xpath}").all()
        if not refs_locs:
            refs_locs = page.locator(
                "xpath=//*[contains(., 'Réf. PROLIANS') and not(.//*[contains(., 'Réf. PROLIANS')])]"
            ).all()

        # Quantité : badge CSS → XPath → fallback large
        qtys_locs = page.locator(Selectors.product_qty_badge).all()
        if not qtys_locs:
            qtys_locs = page.locator(f"xpath={Selectors.prodcut_qty_xpath}").all()
        if not qtys_locs:
            qtys_locs = page.locator(
                "xpath=//*[contains(., 'Qt :') and not(.//*[contains(., 'Qt :')])]"
            ).all()

        for i in range(len(names_locs)):
            try:
                name = clean_text(names_locs[i].inner_text(timeout=3000)) if i < len(names_locs) else ""
                name = name.replace(":", "-")

                ref = ""
                if i < len(refs_locs):
                    ref_txt = refs_locs[i].inner_text(timeout=3000).strip()
                    m_ref = re.search(r"Réf\.?\s+PROLIANS\s*[:\s]\s*(\S+)", ref_txt, re.IGNORECASE)
                    if m_ref:
                        ref = m_ref.group(1)

                qty = ""
                if i < len(qtys_locs):
                    qty_txt = qtys_locs[i].inner_text(timeout=3000).strip()
                    m_qty = re.search(r"Qt\s*:\s*(\d+)", qty_txt, re.IGNORECASE)
                    if m_qty:
                        qty = m_qty.group(1)

                prdt_data.append(f"{ref}:{name}:{qty}")
            except Exception as e:
                log_exception(today, e, f"Produit ligne {i} de {webref}")
    except Exception as e:
        log_exception(today, e, f"Extraction produits {webref}")

    print(prdt_data)

    return {
        "webref":     webref,
        "ref_px":     order.get("internalref", ""),
        "ref_cmd":    ref_cmd,
        "date_cmd":   order.get("date", ""),
        "statut_cmd": order.get("status", ""),
        "prdt_data":  "||".join(prdt_data),
    }
