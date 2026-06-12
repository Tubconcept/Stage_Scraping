"""
Moteur d'extraction DOM Prolians (mode : suivi livraison).

Enchaîne : liste commandes (avec URL de suivi) → fiche détail (produits,
réf. client) → page transporteur (colis, poids, numéro de tracking).

Les transporteurs sont déduits du domaine du lien ou d'un libellé connu
dans le bloc « colis ». L'orchestration SQLite est dans ``scrap_prolians_tracking.py``.
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
from urllib.parse import urlparse, parse_qs
from css_selectors.prolians import Selectors
from core.utils import clean_text

BASE_URL = Selectors.BASE_URL
today    = datetime.today().strftime('%Y-%m-%d')

# Correspondance domaine URL → nom transporteur
CARRIER_MAP = {
    "tnt.fr":        "TNT",
    "geodis":        "GEODIS",
    "kuehne-nagel":  "KUEHNE",
    "chronopost":    "CHRONOPOST",
    "ups.com":       "UPS",
    "dpd.fr":        "DPD",
    "fedex":         "FEDEX",
    "dhl":           "DHL",
}

# Noms transporteurs connus à chercher dans le texte de la page
KNOWN_CARRIERS = list(CARRIER_MAP.values()) + ["DB SCHENKER", "SCHENKER", "COLIS PRIVE", "RELAIS COLIS"]

# URL de suivi Kuehne+Nagel — 516394767 est l'identifiant expéditeur (fixe),
# seul le paramètre ?query= reçoit le numéro de colis.
KUEHNE_TRACKING_URL = "https://mykn.kuehne-nagel.com/public-tracking/shipments?query={n}"


# =============================
# LOG
# =============================

def log_exception(e, commentaire=""):
    os.makedirs("log", exist_ok=True)
    with open(f"log/logException-{today}-TRK.txt", "a", encoding="utf-8") as f:
        ts = datetime.now().strftime('[%Y-%m-%d %H:%M:%S]')
        f.write(f"{ts} {type(e).__name__}: {str(e)}\n")
        for frame in traceback.extract_tb(e.__traceback__):
            f.write(f"  File \"{frame.filename}\", line {frame.lineno}, in {frame.name}\n")
            f.write(f"    {frame.line}\n")
        if commentaire:
            f.write(f"Commentaire: {commentaire}\n")
        f.write("\n")


# =============================
# NAVIGATION COMMANDES
# =============================

def navigate_to_orders(page):
    page.goto(f"{BASE_URL}/customer/account", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    try:
        page.wait_for_selector(Selectors.view_all_orders, timeout=8000)
        page.locator(Selectors.view_all_orders).click()
        page.wait_for_timeout(4000)
    except Exception:
        page.goto(f"{BASE_URL}/customer/account/history/orders/web", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
    try:
        page.wait_for_selector(Selectors.order_row, timeout=20000)
    except Exception:
        pass


def collect_orders_with_tracking(page, date_inf, date_sup):
    """
    Collecte les commandes dans la fenêtre [date_inf, date_sup] + URL de suivi.

    Contrairement à ``collect_orders`` (mode commandes), l'arrêt se fait dès
    qu'une ligne est **antérieure** à ``date_inf`` (liste triée du plus récent
    au plus ancien).
    """
    orders = []
    seen   = set()
    inf = date_inf.date() if hasattr(date_inf, "date") else date_inf
    sup = date_sup.date() if hasattr(date_sup, "date") else date_sup

    while True:
        rows = page.locator(Selectors.order_row).all()
        if not rows:
            break

        stop = False
        for row in rows:
            try:
                webref   = row.locator(Selectors.order_webref).inner_text(timeout=3000).strip()
                date_txt = row.locator(Selectors.order_date).inner_text(timeout=3000).strip()
                date_cmd = datetime.strptime(date_txt, "%d/%m/%Y")

                internalref_el = row.locator(Selectors.order_internalref)
                internalref    = internalref_el.inner_text(timeout=3000).strip() if internalref_el.count() > 0 else ""

                status_el  = row.locator(Selectors.order_status)
                status_txt = status_el.inner_text(timeout=3000).strip() if status_el.count() > 0 else ""

                # Lien "Suivre ma commande" dans la ligne du tableau
                trk_el  = row.locator(Selectors.tracking_button)
                trk_url = None
                if trk_el.count() > 0:
                    href = trk_el.first.get_attribute("href") or ""
                    if href and href not in ("#", ""):
                        trk_url = href if href.startswith("http") else BASE_URL + href

            except Exception as e:
                print(f"Erreur lecture ligne : {e}")
                continue

            # Ignore les commandes trop récentes (hors fenêtre haute)
            if date_cmd.date() > sup:
                continue
            # Commande trop ancienne → on a parcouru toute la plage utile
            if date_cmd.date() < inf:
                stop = True
                break

            if webref not in seen:
                seen.add(webref)
                orders.append({
                    "webref":       webref,
                    "internalref":  internalref,
                    "date":         date_cmd.strftime("%d/%m/%Y"),
                    "status":       status_txt,
                    "tracking_url": trk_url,
                })
                tag = "✓ suivi" if trk_url else "— sans suivi"
                print(f"  {webref} ({internalref}) {date_cmd.date()} [{tag}]")

        if stop:
            break

        next_btn = page.locator(Selectors.next_page)
        if next_btn.count() == 0 or next_btn.get_attribute("disabled") is not None:
            print("Dernière page atteinte")
            break

        first_ref = page.locator(Selectors.order_webref).first.inner_text(timeout=3000).strip()
        next_btn.click()
        page.wait_for_timeout(2000)
        for _ in range(10):
            try:
                if page.locator(Selectors.order_webref).first.inner_text(timeout=1000).strip() != first_ref:
                    break
            except Exception:
                pass
            page.wait_for_timeout(500)

    return orders


# =============================
# HELPERS TRACKING
# =============================

def _extract_tracking_number(link: str) -> str:
    """Extrait le numéro de suivi depuis le query param de l'URL transporteur."""
    if not link:
        return ""
    try:
        params = parse_qs(urlparse(link).query)
        if params:
            return list(params.values())[-1][0]
    except Exception:
        pass
    return ""


def _carrier_from_url(url: str) -> str:
    """Déduit le nom du transporteur depuis l'URL du lien de tracking."""
    for domain, name in CARRIER_MAP.items():
        if domain in url:
            return name
    return ""


def _carrier_from_text(text: str) -> str:
    """Cherche un nom de transporteur connu dans un texte quelconque."""
    text_upper = text.upper()
    # Kuehne+Nagel : variantes avec esperluette, accent, faute de frappe
    for variant in (
        "KUEHNE & NAGEL", "KUEHNE &AMP; NAGEL", "KUEHNE&NAGEL",
        "KÜHNE & NAGEL", "KÜHNE&NAGEL",
        "KUEHNE NAGEL", "KHUENE NAGEL",
        "KUEHNE", "KÜHNE", "KHUENE",
    ):
        if variant in text_upper:
            return "KUEHNE"
    for name in KNOWN_CARRIERS:
        if name in text_upper:
            return name
    return ""


def _tracking_number_from_title(title_text: str) -> str:
    """
    Extrait le numéro depuis un texte du type :
    'Colis #1 - Bon de transport : C3881793937'
    """
    m = re.search(r"Bon de transport\s*[:\-]\s*(\S+)", title_text, re.IGNORECASE)
    return m.group(1) if m else ""


# =============================
# EXTRACTION COLIS
# =============================

def extract_colis_from_page(page) -> tuple:
    """
    Extrait (carrier, weight, tracking_link, tracking_number) depuis la page courante.

    Stratégie en cascade :
      1. Bloc colis (div.colis ou div.blocks) → poids + numéro 'Bon de transport'
      2. Liens <a href> → transporteur via domaine connu + numéro via query param
      3. Corps entier de la page → transporteur par texte, poids, numéro bon transport
      4. KUEHNE : construit l'URL mykn si le transporteur est détecté
    """
    carrier = weight = tracking_link = tracking_number = ""

    # ── 1. Bloc colis : poids + numéro de bon de transport ───────────────────
    block_text = ""
    colis_loc = page.locator("div.colis div.block")
    if colis_loc.count() == 0:
        colis_loc = page.locator(Selectors.tracking_blocks)
    if colis_loc.count() > 0:
        try:
            block_text = colis_loc.first.inner_text()
        except Exception:
            pass

        m_w = re.search(r"(\d+[.,]\d+\s*kg)", block_text, re.IGNORECASE)
        if m_w:
            weight = m_w.group(1).replace(" ", "")

        title_loc = colis_loc.first.locator("div.title")
        if title_loc.count() > 0:
            try:
                tracking_number = _tracking_number_from_title(
                    title_loc.first.inner_text().strip()
                )
            except Exception:
                pass

    # ── 2. Liens <a href> : transporteur par domaine + numéro via query param ─
    all_links = page.locator("a[href]").all()
    for lnk in all_links:
        try:
            href = lnk.get_attribute("href") or ""
            if not href:
                continue
            detected = _carrier_from_url(href)
            if detected:
                tracking_link = href
                carrier = detected
                num_from_url = _extract_tracking_number(href)
                if num_from_url:
                    tracking_number = num_from_url
                break
        except Exception:
            continue

    # ── 3. Corps entier de la page (fallback) ────────────────────────────────
    # Le nom du transporteur ("Transporteur KUEHNE & NAGEL") et le bon de
    # transport ("Bon de transport : C3909483937") se trouvent souvent dans
    # l'en-tête d'expédition, en dehors du bloc colis.
    body_text = ""
    if not carrier or not tracking_number or not weight:
        try:
            body_text = page.locator("body").inner_text()
        except Exception:
            pass

    if body_text:
        if not carrier:
            carrier = _carrier_from_text(body_text)

        if not tracking_number:
            tracking_number = _tracking_number_from_title(body_text)

        if not weight:
            m_w = re.search(r"(\d+[.,]\d+\s*kg)", body_text, re.IGNORECASE)
            if m_w:
                weight = m_w.group(1).replace(" ", "")

    # ── 4. KUEHNE : construire l'URL du portail mykn ─────────────────────────
    if carrier == "KUEHNE" and tracking_number:
        tracking_link = KUEHNE_TRACKING_URL.format(n=tracking_number)

    return carrier, weight, tracking_link, tracking_number


# =============================
# DETAIL COMMANDE
# =============================

def get_order_detail(page, order: dict) -> dict | None:
    """
    Enrichit une commande : références, lignes produits, puis page colis.

    ``tracking_url`` peut déjà être connu depuis la liste ; sinon recherche
    le bouton sur la fiche détail.
    """
    webref = order["webref"]

    # ── 1. Page de détail : ref_cmd + produits ──────────────────────────
    detail_url = f"{BASE_URL}/customer/account/history/orders/web/{webref}"
    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)
    except Exception as e:
        log_exception(e, f"Navigation détail {webref}")
        return None

    # Attendre que React ait rendu les lignes produits (peut prendre quelques secondes)
    try:
        page.wait_for_selector(Selectors.product_name, timeout=8000)
    except Exception:
        pass  # certaines commandes n'ont pas de lignes produits visibles

    ref_cmd = ""
    try:
        ref_cmd = clean_text(
            page.locator(f"xpath={Selectors.client_order_ref_xpath}").inner_text(timeout=5000)
        )
    except Exception:
        pass

    prdt_data = []
    try:
        names_locs = page.locator(Selectors.product_name).all()

        # XPath original → fallback sur tout élément si aucun <span>/<p> ne correspond
        refs_locs = page.locator(f"xpath={Selectors.product_ref_xpath}").all()
        if not refs_locs:
            refs_locs = page.locator(
                "xpath=//*[contains(., 'Réf. PROLIANS') and not(.//*[contains(., 'Réf. PROLIANS')])]"
            ).all()

        # Quantité : badge CSS p.bg-brand-2-50 → XPath → fallback large
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
                log_exception(e, f"Produit {i} de {webref}")
    except Exception as e:
        log_exception(e, f"Produits {webref}")

    # ── 2. Bouton "Suivre ma commande" sur la page de détail ────────────
    tracking_url = order.get("tracking_url")  # déjà capturé depuis la liste

    if not tracking_url:
        try:
            btn = page.locator(Selectors.tracking_button)
            if btn.count() > 0:
                href = btn.first.get_attribute("href") or ""
                if href and href not in ("#", "javascript:void(0)", ""):
                    tracking_url = href if href.startswith("http") else BASE_URL + href
        except Exception:
            pass

    # ── 3. Navigation vers la page de suivi ─────────────────────────────
    carrier = weight = tracking_link = tracking_number = ""

    if tracking_url:
        try:
            page.goto(tracking_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            carrier, weight, tracking_link, tracking_number = extract_colis_from_page(page)
            if carrier or tracking_link:
                print(f"    ✓ {carrier} | {weight} | {tracking_number}")
            else:
                print(f"    Aucune info colis trouvée sur {tracking_url}")
        except Exception as e:
            log_exception(e, f"Page suivi {webref}")
    else:
        # Pas de lien suivi : cherche un bloc colis sur la fiche détail (déjà chargée)
        try:
            carrier, weight, tracking_link, tracking_number = extract_colis_from_page(page)
        except Exception:
            pass

    # DPD et GEODIS sur P3 : numéro extrait non exploitable — on le supprime
    # mais on conserve le nom du transporteur pour la remontée fournisseur.
    if carrier in ("DPD", "GEODIS"):
        tracking_number = ""

    # Normalisation date pour stockage SQLite (format ISO)
    try:
        date_iso = datetime.strptime(order["date"], "%d/%m/%Y").strftime("%Y-%m-%d")
    except Exception:
        date_iso = order.get("date", "")

    return {
        "id_cmd":          webref,
        "ref_cmd":         ref_cmd,
        "date_cmd":        date_iso,
        "statut_cmd":      order.get("status", ""),
        "data_pdt":        "||".join(prdt_data),
        "date_reliquat":   "",
        "weight":          weight,
        "carrier":         carrier,
        "tracking_link":   tracking_link,
        "tracking_number": tracking_number,
    }
