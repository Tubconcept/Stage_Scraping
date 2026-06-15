"""
Moteur CSS du scraper commandes Legallais (site P1).

Rôle :
    Fournit les sélecteurs CSS, les helpers de nettoyage texte et les fonctions
    d'extraction depuis le tableau DataTables et les pages détail commande.

Type : commandes.

Architecture :
    - scraper_legallais_orders.py (ce fichier) = couche CSS / parsing.
    - scrap_legallais_orders.py = orchestrateur (Playwright sync, SQLite).
    Utilise Playwright sync_api (contrairement aux produits Botasaurus).
"""

import sys
from pathlib import Path

from scrapers.Legallais_P1.orders.scrap_legallais_orders import DATE_FORMAT

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import re
import html
from collections import defaultdict
from datetime import datetime, timedelta

from core.logger import setup_logger, log_exception

log   = setup_logger("legallais.orders")
today = datetime.today().strftime('%Y-%m-%d')
week  = datetime.today() - timedelta(days=7)

# ─── URLs ─────────────────────────────────────────────────────────────────────
BASE_URL  = "https://www.legallais.com"
LOGIN_URL = "https://www.legallais.com/user/connection"

# ─── Sélecteurs CSS ───────────────────────────────────────────────────────────
ORDER_DATE_CELL         = "td.sorting_1"
NEXT_PAGE_BUTTON        = "#DataTables_Table_0_next"
ORDER_HEADER_LINES      = "div.o-wrapper > div.user-content div.order-details__heading > div.pro-space-order-details__container div.pro-space-order-details__line"
ORDER_DATE_TEXT         = "p.order-details__heading-date span.u-bold"
PRODUCT_LINK            = "div.order-details__parcel-designation > a.order-details__parcel-designation-link"
PRODUCT_REFERENCE       = "div.order-details__parcel-designation > div.order-details__parcel-designation-ref"
PRODUCT_QUANTITY        = "div.order-details__parcel-quantity-number"
ORDER_STATUS            = "span.c-tag"
RELIQUAT_PRODUCT_BLOCK  = "div.order-details__parcel-designation"
RELIQUAT_PRODUCT_LINK   = "a.order-details__parcel-designation-link"
RELIQUAT_STOCK_LABEL    = "div.c-stock__label"


# ─── Helpers texte ────────────────────────────────────────────────────────────

def nettoyer_texte(texte):
    if not isinstance(texte, str):
        return texte
    texte = html.unescape(texte)  # Décodage des entités HTML (&nbsp; -> " ", etc.)
    texte = texte.replace("\xa0", " ")  # Espace insécable
    texte = texte.replace("\n", " ").replace("\r", "")  # Nettoyage retours à la ligne
    texte = texte.replace("\t", " ")
    texte = texte.replace(":", "")
    texte = texte.replace(",", ".")
    texte = texte.replace(";", ",")
    texte = texte.replace(">=", " supèrieur ou égal à ")
    texte = texte.replace("<=", "inférieur ou égal à ")
    texte = texte.replace(">", " supérieur à ")
    texte = texte.replace("<", " inférieur à ")
    texte = texte.replace("=", " égal à ")
    texte = texte.replace("/", "-")
    texte = texte.replace('"', ' ')
    texte = ' '.join(texte.split())  # Supprime les espaces multiples
    return texte.strip()


def nettoyer_dictionnaire(dico):
    return {k: nettoyer_texte(v) for k, v in dico.items()}


def nettoyer_weight(val):
    if not val:
        return ""
    val = val.replace("kg", "").strip()
    val = val.replace(",", ".")  # standardise décimal
    if re.match(r"\d{1,2}/\d{1,2}/\d{4}", val):
        return ""
    digits_only = re.sub(r"[^\d]", "", val)
    if len(digits_only) == 8:
        try:
            datetime.strptime(digits_only, "%d%m%Y")
            return ""
        except ValueError:
            pass
    try:
        return float(val)
    except ValueError:
        return ""


# ─── Authentification / session ───────────────────────────────────────────────

def connexion(page):
    try:
        page.wait_for_selector(ORDER_DATE_CELL, timeout=4000)
        log.info("Session active détectée")
        return
    except:
        pass
    log.warning("Session expirée — reconnexion manuelle requise")
    print("=" * 50)
    print("  Connectez-vous manuellement dans le navigateur.")
    print("  Une fois sur la page des commandes, revenez")
    print("  ici et appuyez sur Entrée pour continuer.")
    print("=" * 50)
    page.goto(LOGIN_URL)
    input(">> Appuyez sur Entrée après connexion : ")
    page.wait_for_selector("table#DataTables_Table_0 tbody tr", timeout=15000)
    log.info("Session confirmée — démarrage du scraping")


# ─── Collecte commandes ───────────────────────────────────────────────────────

def get_url_cmd(page):
    table = page.locator("table#DataTables_Table_0")
    page.wait_for_selector("tbody tr")
    body = table.locator("tbody tr").all()
    cmd_link = []
    for tr in body:
        link = tr.get_attribute("data-link")
        ref  = nettoyer_texte(tr.locator('td[data-label="Référence"]').inner_text())
        try:
            date_str = tr.locator("td.sorting_1").inner_text().strip()
        except Exception:
            date_str = ""
        cmd  = {"link": link, "ref": ref, "date_str": date_str}
        log.debug(f"Lien cmd : {ref}")
        cmd_link.append(cmd)
    return cmd_link


def check_date(page, datec):
    """Compare la date de la dernière ligne visible à datec (seuil de pagination)."""
    # DataTables trie par date : la dernière ligne = commande la plus ancienne de la page
    date_str = page.locator(ORDER_DATE_CELL).last.inner_text()
    date = datetime.strptime(date_str, DATE_FORMAT)
    return date <= datec


# ─── Extraction reliquat (défini, non appelé dans la version actuelle) ─────────

def get_date_reliquat(page, statut):
    if statut.upper() == "RELIQUAT EN ATTENTE":
        produit_block = page.locator(RELIQUAT_PRODUCT_BLOCK).first
        url_produit = produit_block.locator(RELIQUAT_PRODUCT_LINK).get_attribute("href")
        ref_text = produit_block.locator("div.order-details__parcel-designation-ref").inner_text()
        ref_match = re.search(r"Réf\s*:\s*(\d+)", ref_text)
        ref_produit = ref_match.group(1) if ref_match else None
        log.debug(f"Produit en reliquat détecté : {ref_produit} ({url_produit})")
        new_page = page.context.new_page()
        new_page.goto(url_produit)
        try:
            stock_label = new_page.locator(RELIQUAT_STOCK_LABEL).inner_text()
            dispo_match = re.search(r"Disponible\s+à\s+partir\s+du\s+(\d{2}/\d{2}/\d{4})", stock_label)
            date_dispo = dispo_match.group(1) if dispo_match else "Date non trouvée"
        except:
            date_dispo = "Date non trouvée ou produit non disponible"
        new_page.close()
        return date_dispo
    else:
        return None


# ─── Extraction d'une commande ────────────────────────────────────────────────

def get_info(page, cmd):
    try:
        ref_p1  = cmd['link'].split("/")[-1]
        ref_cmd = cmd['ref']
    except Exception as e:
        log_exception(log, e, "Référencé Erreur")
        ref_p1  = ""
        ref_cmd = ""
    log.debug(f"ref_cmd : {ref_cmd}")
    try:
        try:
            header   = page.locator(ORDER_HEADER_LINES)
            date_raw = header.nth(1).inner_text(timeout=3000).replace("Commandée le", "").strip()
        except:
            date_raw = page.locator(ORDER_DATE_TEXT).first.inner_text().strip()
        m = re.search(r"\d{2}/\d{2}/\d{4}", date_raw)
        date_cmd = m.group(0) if m else nettoyer_texte(date_raw)
    except Exception as e:
        log_exception(log, e, "Erreur de date" + ref_cmd)
    log.debug(f"date_cmd : {date_cmd}")

    prdt_data  = defaultdict(lambda: {"qty": 0, "prix": None, "title": ""})
    final_list = []
    try:
        lis = page.locator("ul.order-details__parcel-list li.order-details__parcel-item").all()
        for li in lis:
            ref_text  = li.locator(PRODUCT_REFERENCE).first.inner_text()
            prdt_qty  = li.locator(PRODUCT_QUANTITY).first.inner_text().strip()

            # Extraction du titre du produit commandé
            try:
                title = nettoyer_texte(li.locator(PRODUCT_LINK).first.inner_text(timeout=1000))
            except Exception:
                title = ""

            # Extraction de la référence
            ref_match   = re.search(r"Réf\s*:\s*(\d+)", ref_text)
            ref_produit = ref_match.group(1) if ref_match else None

            # Nettoyage du prix
            prix_produit = nettoyer_texte(
                li.locator("div.order-details__parcel-net div.order-details__parcel-net-number").inner_text().replace("€", "")
            )

            # Si la quantité est du type "x/y", on prend juste x
            if "/" in prdt_qty:
                prdt_qty = prdt_qty.split("/")[0]

            # Convertir en entier
            try:
                prdt_qty = int(prdt_qty)
            except ValueError:
                prdt_qty = 0

            # Regrouper par ref
            if ref_produit:
                prdt_data[ref_produit]["qty"]  += prdt_qty
                prdt_data[ref_produit]["prix"]  = prix_produit
                if title:
                    prdt_data[ref_produit]["title"] = title

        # Reformater : "ref:title:qty"
        final_list = [
            f"{ref}:{data['title']}:{data['qty']}"
            for ref, data in prdt_data.items()
        ]
        log.debug(f"final_list : {final_list}")

    except Exception as e:
        log_exception(log, e, "Erreur de Produits " + ref_cmd)
    log.debug(f"prdt_data : {prdt_data}")
    try:
        statut = nettoyer_texte(page.locator(ORDER_STATUS).inner_text())
    except Exception as e:
        log_exception(log, e, "Erreur de Statuts " + ref_cmd)
        statut = None
    log.debug(f"statut : {statut}")

    return {
        "ref_p1":    ref_p1,
        "ref_cmd":   ref_cmd,
        "date_cmd":  date_cmd,
        "statut":    statut,
        "prdt_data": "||".join(final_list) if final_list else "",
    }
