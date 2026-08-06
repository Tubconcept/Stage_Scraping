"""
Parse une fiche produit Setin depuis son **HTML statique**. **Pur** (aucun réseau).

Setin est le fournisseur le plus favorable : la fiche embarque tout son contenu
utile dans des **variables JS inline**, exploitables sans exécuter la moindre
ligne de JavaScript :

- ``json_article``  — champs de fiche : marque, désignation, descriptions, PDF ;
- ``json_data``     — **par variante** : réf, réf fabricant (``ref_fournisseur``),
  EAN (``code_barre``), conditionnement, surremballage, images, descriptions ;
- ``json_stock``    — **par variante** : quantité numérique (``{"13": "10.00"}``) ;
- ``json_tarifs``   — **par variante** : ``basePrice`` (prix compte, **numérique**,
  plus aucun libellé « 29,72 € » à parser), ``basePriceHorsPromo``, ``ecoTax``,
  ``haveDeal`` ;
- ``json_variantes_to_sync`` — variantes dont le tarif doit être complété
  (cf. ``setin_tarifs``).

Le DOM ne sert plus que pour trois choses sans équivalent JSON : le **libellé** de
stock, l'**image** de ligne et le **fil d'Ariane**.

⚠️ **Deux pièges, tous deux sources d'erreur silencieuse :**

1. Setin sert de l'**iso-8859-1** (``charset`` dans ``Content-Type``) — d'où
   ``decoder``, et l'interdiction d'utiliser ``reponse.text()`` de Playwright.
2. Les caractéristiques sont un fragment HTML à **apostrophes simples**
   (``<div class='carac'>…``) *dans une chaîne JSON*. Une regex en
   ``class="carac"`` ne matche rien — d'où BeautifulSoup, qui se moque du style
   de guillemets. Même remarque pour ``data-id_var``, écrit tantôt ``="13"``
   tantôt ``='13'`` : c'est du HTML à parser, pas à faire matcher par une regex.

⚠️ Chaque variante devient un **article simple** : aucune colonne de déclinaison
n'est produite (décision 06/08/2026).
"""

from __future__ import annotations

import json
import re
from typing import Any, NamedTuple

from bs4 import BeautifulSoup

from core.texte import nettoyer_texte

# Présent uniquement sur une page servie CONNECTÉE (même marqueur que l'auto-login,
# cf. ``core/login_auto.py`` : ``selecteur_connecte="div.info-perso"``).
_SEL_CONNECTE = "div.info-perso"

# Charset de repli si l'en-tête n'en annonce pas (Setin déclare iso-8859-1).
CHARSET_DEFAUT = "iso-8859-1"
_RE_CHARSET = re.compile(r"charset=([\w-]+)", re.I)

# Variantes réelles : l'id 0 est un gabarit masqué (``stock_variante hide red``).
_ID_GABARIT = "0"


class Variante(NamedTuple):
    """Une ligne de la table de variantes : son id site + son extrait."""

    id_var: str
    extrait: dict


class Fiche(NamedTuple):
    """Résultat du parsing d'une fiche."""

    variantes: list[Variante]
    a_synchroniser: list[int]  # ids dont le tarif manque (cf. ``setin_tarifs``)


# ─── Décodage ─────────────────────────────────────────────────────────────────

def decoder(octets: bytes, content_type: str | None = None) -> str:
    """Décode le corps d'une réponse Setin selon le charset annoncé. **Pur**.

    Setin sert de l'``iso-8859-1`` : décoder en UTF-8 lève ``UnicodeDecodeError``
    dès le premier accent (« Réappro. », « Catégorie »). ``errors="replace"``
    garantit qu'un octet aberrant dégrade un caractère au lieu de perdre la fiche.
    """
    trouve = _RE_CHARSET.search(content_type or "")
    charset = trouve.group(1) if trouve else CHARSET_DEFAUT
    try:
        return octets.decode(charset, errors="replace")
    except LookupError:  # charset annoncé inconnu de Python
        return octets.decode(CHARSET_DEFAUT, errors="replace")


# ─── Variables JS inline ──────────────────────────────────────────────────────

def _litteral(source: str, debut: int) -> str | None:
    """Littéral JSON commençant à ``debut``, par équilibrage des délimiteurs. **Pur**.

    Les accolades **à l'intérieur des chaînes** ne comptent pas — sans quoi une
    désignation comme ``"Coulisse {40}"`` ou un fragment ``<div class='carac'>``
    tronquerait le littéral au mauvais endroit.
    """
    ouvrant = source[debut]
    fermant = {"{": "}", "[": "]"}.get(ouvrant)
    if fermant is None:
        return None
    profondeur = 0
    dans_chaine = False
    echappe = False
    for i in range(debut, len(source)):
        caractere = source[i]
        if dans_chaine:
            if echappe:
                echappe = False
            elif caractere == "\\":
                echappe = True
            elif caractere == '"':
                dans_chaine = False
            continue
        if caractere == '"':
            dans_chaine = True
        elif caractere == ouvrant:
            profondeur += 1
        elif caractere == fermant:
            profondeur -= 1
            if profondeur == 0:
                return source[debut:i + 1]
    return None  # littéral non terminé (HTML tronqué)


def extraire_var(html: str, nom: str) -> Any | None:
    """Valeur d'une variable JS inline ``var <nom> = <json>;``, ou ``None``. **Pur**."""
    trouve = re.search(rf"var\s+{re.escape(nom)}\s*=\s*", html or "")
    if trouve is None or trouve.end() >= len(html):
        return None
    brut = _litteral(html, trouve.end())
    if brut is None:
        return None
    try:
        return json.loads(brut)
    except json.JSONDecodeError:
        return None


def page_deconnectee(html: str) -> bool:
    """Vrai si la page est servie **déconnectée** (session absente/perdue). **Pur**.

    Sans session, ``json_tarifs`` porte les **prix publics** : persister la fiche
    écraserait des prix compte par des prix catalogue (vécu : 108,29 € public
    contre 36,82 € compte sur la même référence). L'appelant saute la fiche.
    """
    return BeautifulSoup(html or "", "html.parser").select_one(_SEL_CONNECTE) is None


def variantes_a_synchroniser(html: str) -> list[int]:
    """Ids dont le tarif n'est PAS dans ``json_tarifs`` (pagination par 10). **Pur**."""
    valeur = extraire_var(html, "json_variantes_to_sync")
    if not isinstance(valeur, list):
        return []
    return [int(v) for v in valeur if isinstance(v, (int, str)) and str(v).lstrip("-").isdigit()]


# ─── Fragments DOM ────────────────────────────────────────────────────────────

def libelles_stock(html: str) -> dict[str, str]:
    """``{id_var: libellé}`` depuis les lignes de stock. **Pur**.

    Le libellé verbatim (« En stock Groupe », « Réappro. Groupe ») est ce que le
    PIM attend en ``product_stock_status`` — on ne le normalise pas, par
    cohérence avec Legallais. Le stock **par agence**
    (``stock_variante_agences``) est ignoré : pas de stock multi-site ici.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    libelles: dict[str, str] = {}
    # Sélecteur de classe CSS = jeton entier : « stock_variante_agences » ne matche pas.
    for bloc in soup.select("div.stock_variante"):
        id_var = (bloc.get("data-id_var") or "").strip()
        if not id_var or id_var == _ID_GABARIT or id_var in libelles:
            continue
        texte = bloc.select_one("span.texte")
        libelle = nettoyer_texte(texte.get_text() if texte else "")
        if libelle:
            libelles[id_var] = libelle
    return libelles


def images_par_variante(html: str) -> dict[str, list[str]]:
    """``{id_var: [url image]}`` depuis les lignes de la table. **Pur**.

    ``json_data[id]["images"]`` ne contient que des **ids** d'image (``[339862]``),
    pas des URLs : la seule URL exploitable est celle du ``<img>`` de la ligne.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    images: dict[str, list[str]] = {}
    for ligne in soup.select("div.ligne_tableau"):
        id_var = (ligne.get("data-id") or "").strip()
        if not id_var or id_var in images:
            continue  # le DOM duplique les lignes : la première porte l'info
        urls = [
            (img.get("src") or "").strip()
            for img in ligne.select("div.photo_variante img")
            if (img.get("src") or "").strip()
        ]
        if urls:
            images[id_var] = urls
    return images


def fil_ariane(html: str) -> list[str]:
    """Catégories du fil d'Ariane, **sans** le dernier élément (le produit). **Pur**."""
    soup = BeautifulSoup(html or "", "html.parser")
    textes = [
        nettoyer_texte(lien.get_text())
        for lien in soup.select("div.fil_ariane_fond .ariane-thematique-link")
    ]
    utiles = [t for t in textes if t]
    return utiles[:-1] if len(utiles) > 1 else utiles


def parser_carac(fragment: str) -> tuple[dict[str, str], str]:
    """Fragment ``<div class='carac'>`` → ``(attributs, texte libre)``. **Pur**.

    Un bloc ``<b>clé</b><span>valeur</span>`` est une **caractéristique** ; un
    bloc ``<span>`` seul est du **texte descriptif** (pas d'attribut sans clé).
    """
    soup = BeautifulSoup(fragment or "", "html.parser")
    attributs: dict[str, str] = {}
    libres: list[str] = []
    for bloc in soup.select("div.carac"):
        cle = bloc.find("b")
        valeur = bloc.find("span")
        libelle = nettoyer_texte(cle.get_text() if cle else "")
        contenu = nettoyer_texte(valeur.get_text() if valeur else "")
        if libelle and contenu:
            attributs.setdefault(libelle, contenu)
        elif contenu:
            libres.append(contenu)
    return attributs, " ".join(libres).strip()


# ─── Mapping ──────────────────────────────────────────────────────────────────

def formater_montant(valeur: Any) -> str:
    """Montant JSON → chaîne décimale, ou ``''``. **Pur**.

    ``basePrice`` arrive en **flottant** (``29.719999999999999``) : on borne à 4
    décimales puis on retire les zéros inutiles. ``0`` est rendu ``''`` : un prix
    nul n'est pas un prix, mieux vaut omettre le champ que d'écrire zéro.
    """
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        if isinstance(valeur, str) and valeur.strip():
            try:
                valeur = float(valeur.replace(",", "."))
            except ValueError:
                return ""
        else:
            return ""
    if not valeur:
        return ""
    return f"{valeur:.4f}".rstrip("0").rstrip(".")


def champs_tarif(tarif: dict | None) -> dict[str, str]:
    """``json_tarifs[id]`` → ``{prix, promotion, eco}``. **Pur**.

    ``basePriceHorsPromo`` n'est un **prix barré** que si une remise est active
    (``haveDeal``) : sinon il vaut ``basePrice``, et publier les deux ferait
    croire à une promotion à 0 %.
    """
    if not isinstance(tarif, dict):
        return {}
    champs = {"prix": formater_montant(tarif.get("basePrice"))}
    if tarif.get("haveDeal"):
        champs["promotion"] = formater_montant(tarif.get("basePriceHorsPromo"))
    eco = formater_montant(tarif.get("ecoTax"))
    if eco:
        champs["eco"] = eco
    return {cle: valeur for cle, valeur in champs.items() if valeur}


def _docs(article: dict, variante: dict) -> list[str]:
    """URLs de documents de la variante puis de la fiche, dédupliquées. **Pur**."""
    candidats = [
        variante.get("url_pdf_technique"),
        variante.get("url_pdf_secu"),
        variante.get("url_pdf"),
        article.get("url_pdf_technique"),
        article.get("url_pdf_secu"),
    ]
    vus: dict[str, None] = {}
    for url in candidats:
        if isinstance(url, str) and url.strip():
            vus.setdefault(url.strip(), None)
    return list(vus)


def _conditionnement(variante: dict) -> str:
    """Conditionnement lisible : quantité + surremballage si différent. **Pur**."""
    cond = variante.get("conditionnement")
    if cond in (None, ""):
        return ""
    texte = str(cond)
    sur = variante.get("surremballage")
    if sur not in (None, "", cond) and str(sur) != texte:
        texte = f"{texte} (surremballage {sur})"
    return texte


def parser_fiche(html: str, url: str) -> Fiche:
    """HTML statique + URL → variantes prêtes pour ``core.f2.element_produit``. **Pur**.

    Une fiche = **une ligne par variante** de ``json_data``, chacune traitée comme
    un article simple. Les champs de fiche (marque, fil d'Ariane, caractéristiques)
    sont partagés ; les champs de variante (réf, EAN, prix, stock, image) sont
    individuels.

    L'URL de chaque variante porte ``?idvar=<ref>`` : c'est ce qui distingue deux
    articles de la même page, et donc ce qui leur donne deux identités
    ``product_uid`` distinctes.
    """
    article = extraire_var(html, "json_article") or {}
    donnees = extraire_var(html, "json_data") or {}
    stocks = extraire_var(html, "json_stock") or {}
    tarifs = extraire_var(html, "json_tarifs") or {}
    if not isinstance(donnees, dict):
        return Fiche([], [])

    attributs_fiche, description_fiche = parser_carac(article.get("description_longue") or "")
    categories = fil_ariane(html)
    libelles = libelles_stock(html)
    images = images_par_variante(html)
    marque_fiche = nettoyer_texte(article.get("nom_marque") or "")

    variantes: list[Variante] = []
    for id_var, variante in donnees.items():
        if not isinstance(variante, dict) or str(id_var) == _ID_GABARIT:
            continue
        ref = nettoyer_texte(variante.get("ref") or "")
        designation = nettoyer_texte(variante.get("designation") or "")
        if not (ref or designation):
            continue

        attributs_var, description_var = parser_carac(variante.get("description_courte") or "")
        # La variante précise ce que la fiche dit en général → elle a le dernier mot.
        attributs = {**attributs_fiche, **attributs_var}
        # Stock : libellé verbatim si le DOM l'a rendu, sinon la quantité brute.
        quantite = stocks.get(id_var) if isinstance(stocks, dict) else None
        stock = libelles.get(str(id_var)) or (str(quantite) if quantite not in (None, "") else "")

        extrait = {
            "url": f"{url}?idvar={ref}" if ref else url,
            "ref": ref,
            "designation": designation or nettoyer_texte(article.get("designation") or ""),
            "ref_fabricant": nettoyer_texte(variante.get("ref_fournisseur") or ""),
            "ean": nettoyer_texte(variante.get("code_barre") or ""),
            "marque": nettoyer_texte(variante.get("nom_marque") or "") or marque_fiche,
            "description": description_var or description_fiche,
            "conditionnement": _conditionnement(variante),
            "stock": stock,
            "categories": categories,
            "images": images.get(str(id_var), []),
            "docs": _docs(article, variante),
            "attributs": attributs,
        }
        extrait.update(champs_tarif(tarifs.get(id_var) if isinstance(tarifs, dict) else None))
        variantes.append(Variante(str(id_var), extrait))

    return Fiche(variantes, variantes_a_synchroniser(html))
