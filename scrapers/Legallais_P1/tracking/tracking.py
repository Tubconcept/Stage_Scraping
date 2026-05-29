import re
import time
import importlib.util as _ilu
from datetime import datetime, timedelta
from pathlib import Path
import html
import csv
import os
import traceback

_spec = _ilu.spec_from_file_location("_legallais_css", Path(__file__).resolve().parents[3] / "css_selectors" / "legallais.py")
_mod = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith('_')})
del _ilu, _spec, _mod
from dataclasses import dataclass                        
from typing import Optional, List
from botasaurus.browser import browser, Driver
from dotenv import load_dotenv
load_dotenv()

today=datetime.today().strftime('%Y-%m-%d')
week=datetime.today() - timedelta(days=7)
csv_path="csv/scrap_p1_Back_"+today+".csv"
BaseUrl="https://www.legallais.com"
LOGIN_URL = "https://www.legallais.com/user/connection"
LEGALLAIS_EMAIL=os.getenv("User")
LEGALLAIS_PASSWORD=os.getenv("Password")
DRY_RUN = True  # True = ne clique pas sur "Supprimer", juste un aperçu
HEADLESS = False  # Mettez True pour exécution silencieuse une fois validé
HUMAN_MODE = True  # mouvements humains pour réduire la détection
WAIT = 1
def nettoyer_texte(texte):
    if not isinstance(texte, str):
        return texte
    texte = html.unescape(texte)  # Décodage des entités HTML (&nbsp; -> " ", etc.)
    texte = texte.replace("\xa0", " ")  # Espace insécable
    texte = texte.replace("\n", " ").replace("\r", "")  # Nettoyage retours à la ligne
    texte = texte.replace("\t", " ")
    texte= texte.replace(":","")
    texte= texte.replace(",",".")
    texte= texte.replace(";", ".")
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
    with open(f"log/logException-{today}-Back.txt", "a", encoding="utf-8") as f:
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
def init_csv(path):
    header = [
        "id_cmd", "ref_cmd", "date_cmd", "statut_cmd", "data_pdt","Date_Reliquat",
        "weight_exp", "carrier_exp", "trackinglink_exp",
        "tracking_exp", 
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
            data_dict.get("dateReliquat", ""),       
            data_dict.get("weight", ""),         # weight_exp
            data_dict.get("transproteur", ""),   # carrier_exp
            data_dict.get("suivi",""),           # trackinglink_exp
            data_dict.get("numero_suivi", ""),   # tracking_exp
               # Date_Reliquat
        ])
def _wait_for(driver: Driver, css: str, timeout: int = WAIT) -> bool:
    try:
        driver.wait_for_element(css, timeout)
        return True
    except Exception:
        return False


def _click_if_present(driver: Driver, css: str) -> bool:
            driver.click(css)
            return True
              
def connexion(driver: Driver, email: str, password: str) -> None:
    assert email and password, "Renseignez LEGALLAIS_EMAIL et LEGALLAIS_PASSWORD"
    # driver.enable_human_mode()
    driver.add_cookies([{"name":"CookiesConsent_ads","value":"true","url": "https://www.legallais.com"},
                        {"name":"CookiesConsent_individualCustomization","value":"true","url": "https://www.legallais.com"},
                        {"name":"CookiesConsent_required","value":"1","url": "https://www.legallais.com"}])
    driver.get(LOGIN_URL)
    
    _wait_for(driver, EMAIL_INPUT)
    driver.type(EMAIL_INPUT, email)
    driver.type(PASSWORD_INPUT, password)
    _click_if_present(driver, LOGIN_BUTTON)
    _wait_for(driver, BREADCRUMB, timeout=10)
    
def check_date(driver: Driver):
    print(len(driver.select_all(ORDER_DATE_CELL)))
    Date_str=driver.select_all(ORDER_DATE_CELL)[-1].text
    Date = datetime.strptime(Date_str, "%d/%m/%Y")
    return Date <= week

def get_url_cmd(driver:Driver):
    # table=driver.select("table#DataTables_Table_0",1)
    # _wait_for(driver,"tbody tr")
    # body=table.select_all("tbody tr",0)
    # cmd_link=[]
    # for tr in body:
    #     link=tr.get_attribute("data-link")
    #     ref=nettoyer_texte(tr.select('td[data-label="Référence"]',0).text)
    #     cmd={"link":link,"ref":ref}
    #     # print(f"Obtention du lien {link}")
    #     cmd_link.append(cmd)
    return driver.run_js("""
        return Array.from(document.querySelectorAll("table#DataTables_Table_0 tbody tr"))
            .map(tr => ({
                link: tr.getAttribute("data-link"),
                ref: tr.querySelector('td[data-label="Référence"]')?.innerText.trim()
            }));
    """)
    return cmd_link


def getDateReliquat(driver:Driver,statut:str):
    if statut.upper() == "RELIQUAT EN ATTENTE":
        # 2. Récupérer l'URL et la référence du produit
        produit_block = driver.select(RELIQUAT_PRODUCT_BLOCK,1)
        url_produit = produit_block.select(RELIQUAT_PRODUCT_LINK,1).get_attribute("href")
        
        ref_text = produit_block.select("div.order-details__parcel-designation-ref",1).text
        ref_match = re.search(r"Réf\s*:\s*(\d+)", ref_text)
        ref_produit = ref_match.group(1) if ref_match else None

        print(f"Produit en reliquat détecté : {ref_produit} ({url_produit})")

        # 3. Ouvrir une nouvelle page
        try:
            new_page = driver.open_link_in_new_tab(url_produit)
            new_page.activate()

            # 4. Attendre la zone de stock et extraire la date
            try:
                stock_label = driver.select(RELIQUAT_STOCK_LABEL,1).text
                dispo_match = re.search(r"Disponible\s+à\s+partir\s+du\s+(\d{2}/\d{2}/\d{4})", stock_label)
                date_dispo = dispo_match.group(1) if dispo_match else "Date non trouvée"
            except:
                date_dispo = "Date non trouvée ou produit non disponible"
            new_page.close()
            return date_dispo
        except:
            new_page.close()
            return None
    else:
        return None
    
def multipliProduct(driver:Driver):
    try:
        items=driver.select(MULTI_PRODUCT_CONTAINER,1).select(MULTI_PRODUCT_ITEM,1)
    except Exception as e:
        log_exception(today,e,"Erreur de multiproduct")
        items=False
    return items

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

    # Essaye de convertir en float si c’est vraiment un poids
    try:
        return float(val)
    except ValueError:
        return ""

def get_Info(driver:Driver,cmd):
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
            header=driver.select_all(ORDER_HEADER_LINES,0)
            date_cmd=nettoyer_texte(header[1].text.replace("Commandée le","").strip())
        except:
            date_cmd=nettoyer_texte(driver.select(ORDER_DATE_TEXT,0).text)
    except Exception as e:
        log_exception(today,e,"Erreur de date"+ref_cmd)
    print(date_cmd)
    try:
        titrePrdt=nettoyer_texte(driver.select(PRODUCT_LINK,0).text.replace(',',".").replace(";",".").replace(":",""))
    except Exception as e:
        log_exception(today,e,"Erreur de Titre "+ref_cmd)
    try:
        ref_text = driver.select(PRODUCT_REFERENCE,0).text
        prdt_qty=driver.select(PRODUCT_QUANTITY,0).text
    
        ref_match = re.search(r"Réf\s*:\s*(\d+)", ref_text)
        ref_produit = ref_match.group(1) if ref_match else None 
        prdt_data=ref_produit+":"+titrePrdt+":"+prdt_qty
    except Exception as e:
        log_exception(today,e,"Erreur de Produits "+ref_cmd)
        prdt_data=""
    print(prdt_data)
    try:
        statut=nettoyer_texte(driver.select(ORDER_STATUS,0).text)
    except Exception as e:
        log_exception(today,e,"Erreur de Statuts "+ref_cmd)
        statut=None
    print(statut)
    try:
        dateReliquat=getDateReliquat(driver,statut)
    except Exception as e:
        log_exception(today,e,"Erreur de Reliquat "+ref_cmd)
        dateReliquat=None
    print(dateReliquat)
    
    transporteur=None
    suivi=None
    numero_suivi=None
    weight=None
    ismultiProduit=multipliProduct(driver)
    
    if driver.is_element_present(TRACKING_MODAL_BUTTON,1):
        try:
            driver.get_element_containing_text('Suivi du colis').click()
            _wait_for(driver, TRACKING_MODAL, timeout=1)
            modal=driver.select(TRACKING_MODAL,0)
            dataTracking=modal.select_all(TRACKING_TABLE_CELLS,0)
            transporteur=nettoyer_texte(dataTracking[1].text)
            if ismultiProduit==False:
                value=nettoyer_texte(dataTracking[3].text.replace("kg",""))
                try:
                    # Vérifie si c'est bien un nombre (avec virgule ou point)
                    weight = nettoyer_weight(value)
                except ValueError:
                    weight = ""
            else:
                weight=None
            link_locator = dataTracking[0].select(TRACKING_LINK,0)
            if link_locator :
                    suivi = link_locator.get_attribute("href")
                    print(suivi)
            else:
                    suivi = None

                # Extraire le numéro de suivi (si lien dispo)
            numero_suivi = None
            if suivi:
                if "chronopost" in suivi:
                        # Exemple: ...listeNumerosLT=MA204090435FR
                    match = re.search(r"chronoNumbers=([A-Z0-9]+)", suivi)
                    transporteur="CHRONOPOST"
                    if match:
                        numero_suivi = match.group(1)
                elif "tnt" in suivi:
                        # Exemple: ...bonTransport=4120002427945375
                    match = re.search(r"bonTransport=(\d+)", suivi)
                    print(match,"tnt")
                    if match:
                        numero_suivi = match.group(1)
                    transporteur="TNT"
                else:
                        print(f"Lien de suivi inconnu ou nouveau format : {suivi}")
        except Exception as e:
            log_exception(today,e,"erreur Modal"+ref_cmd)
    
    return {
        "ref_p1":ref_p1,
        "ref_cmd":ref_cmd,
        "date_cmd":date_cmd,
        "suivi":suivi,
        "statut":statut,
        "dateReliquat":dateReliquat,
        "transproteur":transporteur,
        "numero_suivi":numero_suivi,
        "weight":weight,
        "prdt_data":prdt_data
    }
  

@browser(block_images=True, headless=False,)
def main(driver: Driver, _data=None):
    init_csv(csv_path)
    driver.enable_human_mode()
    print("Ouverture de session…")
    connexion(driver, LEGALLAIS_EMAIL, LEGALLAIS_PASSWORD)
    driver.disable_human_mode()
    driver.get(BaseUrl+"/user/order")
    driver.wait_for_page_to_be(BaseUrl+"/user/order",wait=5)
    check_time=check_date(driver)
    Url_cmd=[]
    Url_cmd.extend(get_url_cmd(driver))

    while check_time!=True:
        try:
            next=driver.select(NEXT_PAGE_BUTTON,0)
            next.scroll_into_view()
            driver.move_mouse_to_element(NEXT_PAGE_BUTTON,1)
            next.click()
            
            
            Url_cmd.extend(get_url_cmd(driver))
            check_time=check_date(driver)
        except Exception as e:
            log_exception(today,e,"érreur de bouclade pour le tab commandes")
            break
    print(f"{len(Url_cmd)} de commande")
    
    for cmd in Url_cmd:
        try:
            driver.get(BaseUrl+cmd['link'],timeout=10)
            driver.wait_for_page_to_be(BaseUrl+cmd['link'],3)
            Commande=get_Info(driver,cmd)
            print(f"Écriture de la commande {Commande} dans le CSV")
            append_to_csv(csv_path,Commande)
        except Exception as e:
                log_exception(today,e,f"érreur page {cmd['link']}")
    print(Url_cmd[-1])

if "__main__"==__name__:
    main()    