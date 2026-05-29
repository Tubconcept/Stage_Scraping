from collections import defaultdict
import re
import importlib.util as _ilu
from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
from pathlib import Path
import html
import csv
import os
import traceback
from dotenv import load_dotenv
load_dotenv()

_spec = _ilu.spec_from_file_location("_legallais_css", Path(__file__).resolve().parents[3] / "css_selectors" / "legallais.py")
_mod = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith('_')})
del _ilu, _spec, _mod

today=datetime.today().strftime('%Y-%m-%d')
week=datetime.today() - timedelta(days=7)
chrome_path="C:/Program Files/Google/Chrome/Application/chrome.exe"

_STAGE_ROOT = Path(__file__).resolve().parents[2]
_PROFILE_DIR = _STAGE_ROOT / "PlaywrightProfile"
if not _PROFILE_DIR.exists():
    import shutil
    _src = _STAGE_ROOT.parent / "Scrap_P1" / "PlaywrightProfile"
    if _src.exists():
        shutil.copytree(str(_src), str(_PROFILE_DIR))
        print(f"[INFO] PlaywrightProfile copié depuis Scrap_P1")
    else:
        print("[AVERTISSEMENT] Aucun PlaywrightProfile trouvé — vous devrez vous connecter manuellement.")
profile_path = str(_PROFILE_DIR)

csv_path="csv/scrap_p1_CMD_"+today+".csv"
BaseUrl="https://www.legallais.com"
LOGIN_URL="https://www.legallais.com/user/connection"
LEGALLAIS_EMAIL=os.getenv("User")
LEGALLAIS_PASSWORD=os.getenv("Password")
def log_exception(today,e,commentaire=""):
    ignorer = [
        "Target page, context or browser has been closed",
        "Browser has been closed",
        "TargetClosedError"
    ]

    # Ignore les erreurs connues liées à une fermeture manuelle
    if any(msg in str(e) for msg in ignorer):
        return

    os.makedirs("log", exist_ok=True)
    with open(f"log/logException-{today}-CMD.txt", "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        f.write(f"{timestamp} {type(e).__name__}: {str(e)}\n")

        # Ajout de la ligne et du fichier source
        tb = traceback.extract_tb(e.__traceback__)
        for frame in tb:
            f.write(f"  File \"{frame.filename}\", line {frame.lineno}, in {frame.name}\n")
            f.write(f"    {frame.line}\n")

        if commentaire:
            f.write(f"Commentaire: {commentaire}\n")
        f.write("\n")

def nettoyer_texte(texte):
    if not isinstance(texte, str):
        return texte
    texte = html.unescape(texte)  # Décodage des entités HTML (&nbsp; -> " ", etc.)
    texte = texte.replace("\xa0", " ")  # Espace insécable
    texte = texte.replace("\n", " ").replace("\r", "")  # Nettoyage retours à la ligne
    texte = texte.replace("\t", " ")
    texte= texte.replace(":","")
    texte= texte.replace(",",".")
    texte= texte.replace(";",",")
    texte= texte.replace(">="," supèrieur ou égal à ")
    texte= texte.replace("<=","inférieur ou égal à ")
    texte= texte.replace(">"," supérieur à ")
    texte= texte.replace("<"," inférieur à ")
    texte= texte.replace("="," égal à ")
    texte=texte.replace("/","-")
    texte=texte.replace('"',' ')# Tabulations
    texte = ' '.join(texte.split())  # Supprime les espaces multiples
    return texte.strip()

def nettoyer_dictionnaire(dico):
    return {k: nettoyer_texte(v) for k, v in dico.items()}

def init_csv(path):
    header = [
        "id_cmd", "ref_cmd", "date_cmd", "statut_cmd", "data_pdt"
    ]
    with open(path, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file, delimiter=';')
        writer.writerow(header)

def append_to_csv(path, data_dict, ):
    with open(path, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file, delimiter=';')

        writer.writerow([
            data_dict.get("ref_p1", ""),         # id_cmd
            data_dict.get("ref_cmd", ""),        # ref_cmd
            data_dict.get("date_cmd", ""),       # date_cmd
            data_dict.get("statut", ""),         # statut_cmd
            data_dict.get("prdt_data", ""),        # data_pdt
        ])

def get_page(playwright, url):
    print(">> Lancement du navigateur")

    browser = playwright.chromium.launch_persistent_context(
        user_data_dir=profile_path,
        channel="chrome",
        headless=False,
    )

    print(">> Navigateur lancé")

    page = browser.new_page()
    page.add_init_script("""Object.defineProperty(navigator, 'webdriver', {get: () => undefined})""")
    print(">> Nouvelle page ouverte")
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.goto(url)
    page.wait_for_load_state("domcontentloaded")
    print(">> Page chargée")
    return browser,page

def connexion(page):
    try:
        page.wait_for_selector(ORDER_DATE_CELL, timeout=4000)
        print(">> Session active détectée")
        return
    except:
        pass
    print(">> Session expirée.")
    print("=" * 50)
    print("  Connectez-vous manuellement dans le navigateur.")
    print("  Une fois sur la page des commandes, revenez")
    print("  ici et appuyez sur Entrée pour continuer.")
    print("=" * 50)
    page.goto(LOGIN_URL)
    input(">> Appuyez sur Entrée après connexion : ")
    page.wait_for_selector("table#DataTables_Table_0 tbody tr", timeout=15000)
    print(">> Session confirmée — démarrage du scraping")

def get_url_cmd(page):
    table=page.locator("table#DataTables_Table_0")
    page.wait_for_selector("tbody tr")
    body=table.locator("tbody tr").all()
    cmd_link=[]
    for tr in body:
        link=tr.get_attribute("data-link")
        ref=nettoyer_texte(tr.locator('td[data-label="Référence"]').inner_text())
        cmd={"link":link,"ref":ref}
        print(f"Obtention du lien {tr}")
        cmd_link.append(cmd)
    return cmd_link

def check_date(page,DateC):
    Date_str=page.locator(ORDER_DATE_CELL).last.inner_text()
    Date = datetime.strptime(Date_str, "%d/%m/%Y")
    return Date <= DateC

def getDateReliquat(page,statut):
    if statut.upper() == "RELIQUAT EN ATTENTE":
        # 2. Récupérer l'URL et la référence du produit
        produit_block = page.locator(RELIQUAT_PRODUCT_BLOCK).first
        url_produit = produit_block.locator(RELIQUAT_PRODUCT_LINK).get_attribute("href")

        ref_text = produit_block.locator("div.order-details__parcel-designation-ref").inner_text()
        ref_match = re.search(r"Réf\s*:\s*(\d+)", ref_text)
        ref_produit = ref_match.group(1) if ref_match else None

        print(f"Produit en reliquat détecté : {ref_produit} ({url_produit})")

        # 3. Ouvrir une nouvelle page
        new_page = page.context.new_page()
        new_page.goto(url_produit)

        # 4. Attendre la zone de stock et extraire la date
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

def nettoyer_weight(val):
    if not val:
        return ""

    # Nettoyage initial : supprime "kg", espaces, etc.
    val = val.replace("kg", "").strip()
    val = val.replace(",", ".")  # standardise décimal

    # Cas 1 : format date explicite (avec /)
    if re.match(r"\d{1,2}/\d{1,2}/\d{4}", val):
        return ""

    # Cas 2 : date numérique (8 chiffres ou float genre 18062025.0)
    digits_only = re.sub(r"[^\d]", "", val)
    if len(digits_only) == 8:
        try:
            # essaie de parser comme date
            datetime.strptime(digits_only, "%d%m%Y")
            return ""  # c'était bien une date
        except ValueError:
            pass

    # Essaye de convertir en float si c'est vraiment un poids
    try:
        return float(val)
    except ValueError:
        return ""

def get_Info(page,cmd):
    try:
        ref_p1=cmd['link'].split("/")[-1]

        ref_cmd=cmd['ref']
    except Exception as e:
        log_exception(today,e,"Référencé Erreur")
        ref_p1=""
        ref_cmd=""
    print(ref_cmd)
    try:
        try:
            header=page.locator(ORDER_HEADER_LINES)
            date_cmd=nettoyer_texte(header.nth(1).inner_text(timeout=3000).replace("Commandée le","").strip())
        except:
            date_cmd=nettoyer_texte(page.locator(ORDER_DATE_TEXT).first.inner_text())
    except Exception as e:
        log_exception(today,e,"Erreur de date"+ref_cmd)
    print(date_cmd)
    try:
        titrePrdt=nettoyer_texte(page.locator(PRODUCT_LINK).first.inner_text().replace(',',".").replace(";",".").replace(":",""))
    except Exception as e:
        log_exception(today,e,"Erreur de Titre "+ref_cmd)

    prdt_data = defaultdict(lambda: {"qty": 0, "prix": None})
    final_list = []
    try:
        lis = page.locator("ul.order-details__parcel-list li.order-details__parcel-item").all()
        for li in lis:
            ref_text = li.locator(PRODUCT_REFERENCE).first.inner_text()
            prdt_qty = li.locator(PRODUCT_QUANTITY).first.inner_text().strip()

            # Extraction de la référence
            ref_match = re.search(r"Réf\s*:\s*(\d+)", ref_text)
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
                prdt_data[ref_produit]["qty"] += prdt_qty
                prdt_data[ref_produit]["prix"] = prix_produit

        # Reformater en liste
        final_list = [f"{ref}:{data['qty']}:{data['prix']}" for ref, data in prdt_data.items()]
        print(final_list)

    except Exception as e:
        log_exception(today,e,"Erreur de Produits "+ref_cmd)
    print(prdt_data)
    try:
        statut=nettoyer_texte(page.locator(ORDER_STATUS).inner_text())
    except Exception as e:
        log_exception(today,e,"Erreur de Statuts "+ref_cmd)
        statut=None
    print(statut)

    return {
        "ref_p1":ref_p1,
        "ref_cmd":ref_cmd,
        "date_cmd":date_cmd,
        "statut":statut,
        "prdt_data":",".join(final_list) if final_list else ""
    }



def main():
    inputSup=input("Fournisser la date supérieur de l'intervalle de temps veuillez écrire une date au format d/m/yyyy ")
    inputInf=input("Fournisser la date inférieur de l'intervalle de temps veuillez écrire une date au format d/m/yyyy ")

    try: DateInf=datetime.strptime(inputInf,"%d/%m/%Y")
    except: DateInf=datetime.today() - timedelta(days=3)

    try:DateSup=datetime.strptime(inputSup,"%d/%m/%Y")
    except:DateSup=datetime.today()
    with sync_playwright() as p:
        browser,page=get_page(p,BaseUrl+"/user/order")
        connexion(page)
        init_csv(csv_path)
        os.makedirs("csv", exist_ok=True)
        Url_cmd=[]
        check_time_sup=check_date(page,DateSup)
        while check_time_sup!=True:
            try:
                old_first_row = page.locator("tbody tr").first
                old_text = old_first_row.inner_text()
                page.locator(NEXT_PAGE_BUTTON).click()
                page.wait_for_function(
                    """(oldText) => {
                        const firstRow = document.querySelector('tbody tr');
                        return firstRow && firstRow.innerText !== oldText;
                    }""",
                    arg=old_text
                )
                check_time_sup=check_date(page,DateSup)
            except Exception as e:
                log_exception(today,e,"érreur de bouclage pour le tab commandes")
                break
        Url_cmd.extend(get_url_cmd(page))
        check_time_inf=check_date(page,DateInf)
        while check_time_inf!=True:
            try:
                old_first_row = page.locator("tbody tr").first
                old_text = old_first_row.inner_text()
                page.locator(NEXT_PAGE_BUTTON).click()
                page.wait_for_function(
                    """(oldText) => {
                        const firstRow = document.querySelector('tbody tr');
                        return firstRow && firstRow.innerText !== oldText;
                    }""",
                    arg=old_text
                )
                Url_cmd.extend(get_url_cmd(page))
                check_time_inf=check_date(page,DateInf)
            except Exception as e:
                log_exception(today,e,"érreur de bouclage pour le tab commandes")
                break

        print(f"{len(Url_cmd)} de commande")

        for cmd in Url_cmd:
            try:
                page.goto(BaseUrl+cmd['link'])
                page.wait_for_load_state("domcontentloaded")
                Commande=get_Info(page,cmd)
                print(Commande)
                append_to_csv(csv_path,Commande)
            except Exception as e:
                log_exception(today,e,f"érreur page {cmd['link']}")
        print(Url_cmd[-1])


def cmd_main():
    with sync_playwright() as p:
        browser,page=get_page(p,BaseUrl+"/user/order/view/33477465")
        init_csv(csv_path)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_load_state("load")
        Commande=get_Info(page,{"link":"/user/order/view/33477465","ref":"20250611000848"})
        print(Commande)
        append_to_csv(csv_path,Commande)
        page.goto(BaseUrl+"/user/order/view/33477814 ")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_load_state("load")
        Commande=get_Info(page,{"link":"/user/order/view/33477814 ","ref":"20250610000770"})
        print(Commande)
        append_to_csv(csv_path,Commande)
        input("wait")
        page.goto(BaseUrl+"/user/order/view/33461761")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_load_state("load")
        Commande=get_Info(page,{"link":"/user/order/view/33461761","ref":"20250609000693"})
        print(Commande)
        append_to_csv(csv_path,Commande)
        input("wait")

if __name__=="__main__":
    main()
