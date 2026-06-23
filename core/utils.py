"""
Fonctions utilitaires partagées par tous les scrapers.

Ce module fournit :
  - Nettoyage de texte extrait du DOM (HTML, espaces, symboles)
  - Extraction sécurisée via Playwright (safe_get_text, safe_get_attribute)
  - Variantes Botasaurus (safe_get_text_bot) pour Legallais produits
  - Helpers fichiers (répertoires, noms horodatés)
"""

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Dict

from .logger import logger


# ═══════════════════════════════════════════════════════════════════════════════
# NETTOYAGE DE TEXTE
# Normalise les chaînes avant insertion CSV / SQLite
# ═══════════════════════════════════════════════════════════════════════════════

def clean_text(text: Any) -> str:
    """Nettoie une chaîne extraite du web pour l'export CSV.

    - Décode les entités HTML (&nbsp;, &euro;…)
    - Remplace les espaces insécables et sauts de ligne
    - Supprime ou remplace certains symboles problématiques pour le CSV (;)

    Args:
        text: Valeur brute (souvent str, parfois None ou autre type).

    Returns:
        Chaîne nettoyée, ou "" si l'entrée n'est pas une chaîne.
    """
    if not isinstance(text, str):
        return ""

    text = html.unescape(text)

    # Caractères invisibles fréquents dans le HTML français
    text = text.replace("\xa0", " ")
    text = text.replace("\n", " ")
    text = text.replace("\r", "")
    text = text.replace("\t", " ")

    # Seul le ; est réellement problématique comme délimiteur CSV — le reste est
    # laissé intact car l'export utilise QUOTE_ALL (tous les champs sont entre guillemets).
    replacements = {
        ";": ".",
        ">=": " supérieur ou égal à ",
        "<=": " inférieur ou égal à ",
        ">": " supérieur à ",
        "<": " inférieur à ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = ' '.join(text.split())
    return text.strip()


# Prix FR : « 1 752,90 € » → numérique. ``\d[\d\s]*`` capture l'entier avec
# d'éventuels espaces de milliers — ``\s`` couvre TOUTES les variantes Unicode :
# espace normal, insécable \xa0, fine   et **fine insécable  ** (celle
# réellement utilisée par certains sites, ex. Prolians). ``(?:[.,]\d+)?`` = décimale.
_PRICE_RE = re.compile(r"\d[\d\s]*(?:[.,]\d+)?")


def normalize_price(raw: Any) -> str:
    """Normalise un prix brut en numérique « dot-decimal », ou '' si aucun nombre.

    Gère le symbole €, **tous** les espaces séparateurs de milliers (normal,
    insécable \\xa0, fine \\u2009, fine insécable \\u202f) et la virgule décimale :

        « 1 752,90 € » → « 1752.90 »   « 602,57€ » → « 602.57 »   « Sur devis » → « »

    À utiliser par TOUS les scrapers pour un format de prix homogène — sinon un
    ``float()`` à l'import casse (« 602,57 », « 12.30 € »), ou pire un séparateur
    de milliers tronque « 1 752,90 » à « 1 ».
    """
    if not isinstance(raw, str):
        return ""
    m = _PRICE_RE.search(raw)
    if not m:
        return ""
    return re.sub(r"\s", "", m.group(0)).replace(",", ".")


def clean_dict(data: Dict[str, Any]) -> Dict[str, str]:
    """Applique clean_text à toutes les valeurs d'un dictionnaire produit."""
    return {key: clean_text(value) for key, value in data.items()}


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACTION SÉCURISÉE — PLAYWRIGHT (async ou sync)
# Retourne "" en cas d'élément absent ou timeout (pas d'exception propagée)
# ═══════════════════════════════════════════════════════════════════════════════

def safe_get_text(locator, timeout: int = 5000) -> str:
    """Attend la visibilité d'un locator Playwright puis retourne son texte nettoyé."""
    try:
        locator.wait_for(state="visible", timeout=timeout)
        text = locator.inner_text(timeout=timeout)
        return clean_text(text)
    except Exception as e:
        logger.debug(f"Erreur lors de l'extraction de texte: {e}")
        return ""


def safe_get_text_bot(element, wait: int = 1) -> str:
    """Même rôle que safe_get_text pour un élément Botasaurus (Legallais)."""
    try:
        text = element.text(wait=wait)
        return clean_text(text)
    except Exception as e:
        logger.debug(f"Erreur lors de l'extraction de texte: {e}")
        return ""


def safe_get_attribute_bot(element, attribute: str) -> str:
    """Extrait un attribut HTML depuis un élément Botasaurus."""
    try:
        return element.get_attribute(attribute) or ""
    except Exception as e:
        logger.debug(f"Erreur lors de l'extraction d'attribut '{attribute}': {e}")
        return ""


def safe_get_attribute(locator, attribute: str, timeout: int = 5000) -> str:
    """Extrait un attribut (href, src, alt…) depuis un locator Playwright."""
    try:
        locator.wait_for(state="visible", timeout=timeout)
        return locator.get_attribute(attribute) or ""
    except Exception as e:
        logger.debug(f"Erreur lors de l'extraction d'attribut '{attribute}': {e}")
        return ""


def extract_list_from_locators(locators: list, attribute: Optional[str] = None) -> list:
    """Parcourt une liste de locateurs et collecte texte ou attribut de chacun.

    Args:
        locators: Liste d'objets Locator Playwright.
        attribute: Si renseigné, extrait cet attribut ; sinon le texte visible.

    Returns:
        Liste de chaînes nettoyées (valeurs vides exclues).
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


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES FICHIERS
# ═══════════════════════════════════════════════════════════════════════════════

def ensure_directory(path: Path) -> None:
    """Crée le répertoire et ses parents si nécessaire."""
    path.mkdir(exist_ok=True, parents=True)


def get_timestamped_filename(basename: str, extension: str = "csv") -> str:
    """Génère un nom de fichier avec la date du jour (ex. export_2026-06-05.csv)."""
    today = datetime.today().strftime('%Y-%m-%d')
    return f"{basename}_{today}.{extension}"
