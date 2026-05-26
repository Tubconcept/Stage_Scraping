import html
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Dict

from .logger import logger


# === NETTOYAGE DE TEXTE ===
def clean_text(text: Any) -> str:
    """
    Nettoie le texte en supprimant les caractères HTML, espacements, etc.
    
    Args:
        text: Texte à nettoyer
        
    Returns:
        Texte nettoyé
    """
    if not isinstance(text, str):
        return ""
    
    # Décodage HTML
    text = html.unescape(text)
    
    # Suppression caractères invisibles
    text = text.replace("\xa0", " ")  # Espace insécable
    text = text.replace("\n", " ")
    text = text.replace("\r", "")
    text = text.replace("\t", " ")
    
    # Remplacements de symboles (à utiliser avec prudence)
    replacements = {
        "€": "",
        "/": "-",
        '"': " ",
        ":":"",
        ";":".",
        ",":".",
        ">=":" supèrieur ou égal à ",
        "<=":"inférieur ou égal à ",
        ">":" supérieur à ",
        "<":" inférieur à ",
        "=":" égal à ",
        '"':' '
    }
    
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    # Suppression espaces multiples
    text = ' '.join(text.split())
    
    return text.strip()


def clean_dict(data: Dict[str, Any]) -> Dict[str, str]:
    """Nettoie toutes les valeurs d'un dictionnaire."""
    return {key: clean_text(value) for key, value in data.items()}


# === EXTRACTION DE DONNÉES ===
def safe_get_text(locator, timeout: int = 5000) -> str:
    """
    Extrait le texte de façon sécurisée d'un locateur
    
    Args:
        locator: Locateur Playwright
        timeout: Timeout en ms
        
    Returns:
        Texte extrait et nettoyé
    """
    try:
        locator.wait_for(state="visible", timeout=timeout)
        text = locator.inner_text(timeout=timeout)
        return clean_text(text)
    except Exception as e:
        logger.debug(f"Erreur lors de l'extraction de texte: {e}")
        return ""


def safe_get_text_bot(element,wait:int=1)->str:
    """
    Extrait le texte de façon sécurisée d'un locateur
    
    Args:
        element: Element WEB Botasaurus
        wait: Timeout en s
        
    Returns:
        Texte extrait et nettoyé
    """
    try:

        text = element.text(wait=wait)
        return clean_text(text)
    except Exception as e:
        logger.debug(f"Erreur lors de l'extraction de texte: {e}")
        return ""


def safe_get_attribute_bot(element,attribute: str,wait:int=1)->str:
    """
    Extrait un attribut de façon sécurisée
    
    Args:
        element: element Web Botasaurus
        attribute: Nom de l'attribut
        tiwaitmeout: Timeout en s
        
    Returns:
        Valeur de l'attribut
    """
    try:
        return element.get_attribute(attribute) or ""
    except Exception as e:
        logger.debug(f"Erreur lors de l'extraction d'attribut '{attribute}': {e}")
        return ""


def safe_get_attribute(locator, attribute: str, timeout: int = 5000) -> str:
    """
    Extrait un attribut de façon sécurisée
    
    Args:
        locator: Locateur Playwright
        attribute: Nom de l'attribut
        timeout: Timeout en ms
        
    Returns:
        Valeur de l'attribut
    """
    try:
        locator.wait_for(state="visible", timeout=timeout)
        return locator.get_attribute(attribute) or ""
    except Exception as e:
        logger.debug(f"Erreur lors de l'extraction d'attribut '{attribute}': {e}")
        return ""


def extract_list_from_locators(locators: list, attribute: Optional[str] = None) -> list:
    """
    Extrait une liste de valeurs depuis une liste de locateurs
    
    Args:
        locators: Liste de locateurs Playwright
        attribute: Attribut à extraire (None = texte)
        
    Returns:
        Liste des valeurs extraites
    """
    result = []
    for locator in locators:
        try:
            if attribute:
                value = locator.get_attribute(attribute)
            else:
                value = locator.inner_text().strip()
            
            if value:
                result.append(clean_text(value))
        except Exception as e:
            logger.debug(f"Erreur extraction: {e}")
    
    return result

# === UTILITAIRES FICHIERS ===
def ensure_directory(path: Path) -> None:
    """S'assure qu'un répertoire existe"""
    path.mkdir(exist_ok=True, parents=True)


def get_timestamped_filename(basename: str, extension: str = "csv") -> str:
    """
    Génère un nom de fichier avec timestamp
    
    Args:
        basename: Nom de base du fichier
        extension: Extension du fichier
        
    Returns:
        Nom de fichier avec timestamp
    """
    today = datetime.today().strftime('%Y-%m-%d')
    return f"{basename}_{today}.{extension}"

