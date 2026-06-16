"""
Moteur CSS du scraper suivi / tracking Legallais (site P1).

Rôle :
    Extraction des données de suivi depuis la page commande : modal « Suivi du colis »,
    parsing transporteur/numéro depuis l'URL, poids, date reliquat et lignes article.

Type : suivi (tracking).

Architecture :
    - scraper_legallais_tracking.py (ce fichier) = couche CSS / parsing Botasaurus.
    - scrap_legallais_tracking.py = orchestrateur (main @browser, SQLite).
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import re
import html
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List

from botasaurus.browser import Driver
from dotenv import load_dotenv

from core.logger import setup_logger, log_exception

load_dotenv()

log   = setup_logger("legallais.tracking")
today = datetime.today().strftime('%Y-%m-%d')
week  = datetime.today() - timedelta(days=7)

# ─── URLs ─────────────────────────────────────────────────────────────────────
BASE_URL  = "https://www.legallais.com"
LOGIN_URL = "https://www.legallais.com/user/connection"

# ─── Timeout par défaut ───────────────────────────────────────────────────────
WAIT = 1

# ─── Sélecteurs CSS ───────────────────────────────────────────────────────────
EMAIL_INPUT             = "input[name='connexion[login]'], input[type='text'], #connection-id"
PASSWORD_INPUT          = "input[name='connexion[password]'], input[type='password'], #connection-passwd"
LOGIN_BUTTON            = "button[data-action='components--connection#sendConnection']"
BREADCRUMB              = "ol.c-breadcrumb"
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
MULTI_PRODUCT_CONTAINER = "div.c-card div.o-layout__item"
MULTI_PRODUCT_ITEM      = "li.order-details__parcel-item"
TRACKING_MODAL_BUTTON   = "a.modal-link"
TRACKING_MODAL          = ".mfp-content"
TRACKING_TABLE_CELLS    = "table tbody tr.order-details__modal-tracking-info-content td"
TRACKING_LINK           = "a"


# ─── Helpers texte ────────────────────────────────────────────────────────────

def nettoyer_texte(texte):
    if not isinstance(texte, str):
        return texte
    texte = html.unescape(texte)
    texte = texte.replace("\xa0", " ")
    texte = texte.replace("\n", " ").replace("\r", "")
    texte = texte.replace("\t", " ")
    texte = texte.replace(":", "")
    texte = texte.replace(",", ".")
    texte = texte.replace(";", ".")
    texte = texte.replace(">=", " supèrieur ou égal à ")
    texte = texte.replace("<=", "inférieur ou égal à ")
    texte = texte.replace(">", " supérieur à ")
    texte = texte.replace("<", " inférieur à ")
    texte = texte.replace("=", " égal à ")
    texte = texte.replace("/", "-")
    texte = texte.replace('"', ' ')
    texte = ' '.join(texte.split())
    return texte.strip()


def nettoyer_dictionnaire(dico):
    return {k: nettoyer_texte(v) for k, v in dico.items()}


def nettoyer_weight(val):
    if not val:
        return ""
    val = val.replace("kg", "").strip()
    val = val.replace(",", ".")
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


# ─── Helpers navigation ───────────────────────────────────────────────────────

def _wait_for(driver: Driver, css: str, timeout: int = WAIT) -> bool:
    try:
        driver.wait_for_element(css, timeout)
        return True
    except Exception:
        return False


def _click_if_present(driver: Driver, css: str) -> bool:
    driver.click(css)
    return True


# ─── Authentification ─────────────────────────────────────────────────────────
URL_SITE = "https://www.legallais.com" 
def connexion(driver: Driver, email: str, password: str) -> None:
    assert email and password, "Renseignez LEGALLAIS_EMAIL et LEGALLAIS_PASSWORD"
    from auth.legallais.cookie_manager_legallais import (
        load_cookies_for_driver, save_cookies_from_driver,
    )

    # Cookies de consentement (toujours injectés)
    driver.add_cookies([
        {"name": "CookiesConsent_ads",                     "value": "true", "url": URL_SITE},
        {"name": "CookiesConsent_individualCustomization", "value": "true", "url": URL_SITE},
        {"name": "CookiesConsent_required",                "value": "1",    "url": URL_SITE},
    ])

    # Tenter de restaurer la session du jour
    if load_cookies_for_driver(driver):
        driver.get(BASE_URL)
        _wait_for(driver, BREADCRUMB, timeout=5)
        if not driver.is_element_present(EMAIL_INPUT):
            log.info("Session restaurée — connexion ignorée.")
            return
        log.info("Session expirée — nouvelle connexion en cours...")

    # Login complet
    log.info("Connexion en cours...")
    driver.get(LOGIN_URL)
    _wait_for(driver, EMAIL_INPUT)
    driver.type(EMAIL_INPUT, email)
    driver.type(PASSWORD_INPUT, password)
    _click_if_present(driver, LOGIN_BUTTON)
    _wait_for(driver, BREADCRUMB, timeout=10)

    # Sauvegarder la nouvelle session
    save_cookies_from_driver(driver)


# ─── Collecte des commandes ───────────────────────────────────────────────────

def check_date(driver: Driver):
    """Retourne True quand la dernière commande visible est antérieure à la fenêtre (7 j)."""
    log.debug(f"Nombre de lignes de date : {len(driver.select_all(ORDER_DATE_CELL))}")
    date_str = driver.select_all(ORDER_DATE_CELL)[-1].text
    date = datetime.strptime(date_str, "%d/%m/%Y")
    return date <= week


def get_url_cmd(driver: Driver):
    return driver.run_js("""
        return Array.from(document.querySelectorAll("table#DataTables_Table_0 tbody tr"))
            .map(tr => ({
                link: tr.getAttribute("data-link"),
                ref: tr.querySelector('td[data-label="Référence"]')?.innerText.trim()
            }));
    """)

# ─── Extraction reliquat ──────────────────────────────────────────────────────

def get_date_reliquat(driver: Driver, statut: str):
    if statut.upper() == "RELIQUAT EN ATTENTE":
        produit_block = driver.select(RELIQUAT_PRODUCT_BLOCK, 1)
        url_produit = produit_block.select(RELIQUAT_PRODUCT_LINK, 1).get_attribute("href")
        ref_text = produit_block.select("div.order-details__parcel-designation-ref", 1).text
        ref_match = re.search(r"Réf\s*:\s*(\d+)", ref_text)
        ref_produit = ref_match.group(1) if ref_match else None
        log.debug(f"Produit en reliquat détecté : {ref_produit} ({url_produit})")
        try:
            new_page = driver.open_link_in_new_tab(url_produit)
            new_page.activate()
            try:
                stock_label = driver.select(RELIQUAT_STOCK_LABEL, 1).text
                dispo_match = re.search(r"Disponible\s+à\s+partir\s+du\s+(\d{2}/\d{2}/\d{4})", stock_label)
                date_dispo = dispo_match.group(1) if dispo_match else "Date non trouvée"
            except Exception:
                date_dispo = "Date non trouvée ou produit non disponible"
            new_page.close()
            return date_dispo
        except Exception:
            new_page.close()
            return None
    else:
        return None


def multipli_product(driver: Driver):
    try:
        items = driver.select(MULTI_PRODUCT_CONTAINER, 1).select(MULTI_PRODUCT_ITEM, 1)
    except Exception as e:
        log_exception(log, e, "Erreur de multiproduct")
        items = False
    return items


# ─── Extraction d'une commande ────────────────────────────────────────────────

def get_info(driver: Driver, cmd):
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
            header   = driver.select_all(ORDER_HEADER_LINES, 0)
            date_raw = header[1].text.replace("Commandée le", "").strip()
        except:
            date_raw = driver.select(ORDER_DATE_TEXT, 0).text.strip()
        m = re.search(r"\d{2}/\d{2}/\d{4}", date_raw)
        date_cmd = m.group(0) if m else nettoyer_texte(date_raw)
    except Exception as e:
        log_exception(log, e, "Erreur de date" + ref_cmd)
    log.debug(f"date_cmd : {date_cmd}")
    try:
        titre_prdt = nettoyer_texte(
            driver.select(PRODUCT_LINK, 0).text.replace(',', ".").replace(";", ".").replace(":", "-")
        )
    except Exception as e:
        log_exception(log, e, "Erreur de Titre " + ref_cmd)
    try:
        ref_text  = driver.select(PRODUCT_REFERENCE, 0).text
        prdt_qty  = driver.select(PRODUCT_QUANTITY, 0).text
        ref_match = re.search(r"Réf\s*:\s*(\d+)", ref_text)
        ref_produit = ref_match.group(1) if ref_match else None
        prdt_data   = f"{ref_produit or ''}:{titre_prdt}:{prdt_qty.strip()}"
    except Exception as e:
        log_exception(log, e, "Erreur de Produits " + ref_cmd)
        prdt_data = ""
    log.debug(f"prdt_data : {prdt_data}")
    try:
        statut = nettoyer_texte(driver.select(ORDER_STATUS, 0).text)
    except Exception as e:
        log_exception(log, e, "Erreur de Statuts " + ref_cmd)
        statut = None
    log.debug(f"statut : {statut}")
    try:
        date_reliquat = get_date_reliquat(driver, statut)
    except Exception as e:
        log_exception(log, e, "Erreur de Reliquat " + ref_cmd)
        date_reliquat = None
    log.debug(f"date_reliquat : {date_reliquat}")

    transporteur  = None
    suivi         = None
    numero_suivi  = None
    weight        = None

    # Ouverture de la modale de suivi colis si le bouton est présent sur la page
    if driver.is_element_present(TRACKING_MODAL_BUTTON, 1):
        try:
            driver.get_element_containing_text('Suivi du colis').click()
            _wait_for(driver, TRACKING_MODAL, timeout=1)
            modal        = driver.select(TRACKING_MODAL, 0)
            data_tracking = modal.select_all(TRACKING_TABLE_CELLS, 0)
            transporteur = nettoyer_texte(data_tracking[1].text)
            # Extraction du poids avec unité (ex: "3.6 kg"), indépendant du type de commande
            weight = None
            if len(data_tracking) > 3:
                raw_w = " ".join(data_tracking[3].text.split())
                m_w = re.search(r"(\d+[.,]\d+|\d+)\s*kg", raw_w, re.IGNORECASE)
                weight = f"{m_w.group(1).replace(',', '.')} kg" if m_w else None
            link_locator = data_tracking[0].select(TRACKING_LINK, 0)
            if link_locator:
                suivi = link_locator.get_attribute("href")
                log.debug(f"suivi : {suivi}")
            else:
                suivi = None

            numero_suivi = None
            if suivi:
                if "chronopost" in suivi:
                    match = re.search(r"chronoNumbers=([A-Z0-9]+)", suivi)
                    transporteur = "CHRONOPOST"
                    if match:
                        numero_suivi = match.group(1)
                elif "tnt" in suivi:
                    match = re.search(r"bonTransport=(\d+)", suivi)
                    log.debug(f"match TNT : {match}")
                    if match:
                        numero_suivi = match.group(1)
                    transporteur = "TNT"
                elif "normatrans" in suivi.lower() or "normatrans" in transporteur.lower():
                    transporteur = "NORMATRANS"
                    match = re.search(r"sReference=([A-Z0-9]+)", suivi, re.IGNORECASE)
                    if match:
                        numero_suivi = match.group(1)
                        suivi = "https://normatrans.teliway.com/appli/vnormatrans/tracking/suivi.php?code=normatrans&clef=16&rem=LEGALL14C&sReference=" + numero_suivi
                elif "traplus" in suivi.lower() or "valot" in transporteur.lower():
                    transporteur = "VALOT"
                    match = re.search(r"REFE=([A-Z0-9]+)", suivi, re.IGNORECASE)
                    if match:
                        numero_suivi = match.group(1)
                        suivi = "https://www.traplus.com/twt/cgi-bin/rapacc.pgm?LIEU=VALOT&CACCR=SVPLEGALLAIS&REFE=" + numero_suivi
                else:
                    log.warning(f"Lien de suivi inconnu ou nouveau format : {suivi}")
        except Exception as e:
            log_exception(log, e, "erreur Modal" + ref_cmd)

    return {
        "ref_p1":       ref_p1,
        "ref_cmd":      ref_cmd,
        "date_cmd":     date_cmd,
        "suivi":        suivi,
        "statut":       statut,
        "dateReliquat": date_reliquat,
        "transproteur": transporteur,
        "numero_suivi": numero_suivi,
        "weight":       weight,
        "prdt_data":    prdt_data,
    }
