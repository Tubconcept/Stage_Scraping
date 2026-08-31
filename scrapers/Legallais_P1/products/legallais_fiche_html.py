"""
Parse une fiche produit Legallais depuis son **HTML statique**. **Pur** (aucun réseau).

La voie légère récupère les fiches en HTTP simple, sans rendu navigateur : on lit
donc le **maximum** de ce que la page contient **déjà**, sans JavaScript exécuté.
Seuls **prix** et **stock** restent vides (injectés en XHR côté site) et viennent
de l'enrichissement ``legallais_article_infos``.

Sources exploitées :
  - ``meta[property=og:title]`` / ``meta[itemprop=name]`` → désignation
  - ``[itemprop=brand] [itemprop=name]`` + ``a.u-display-contents img`` → marque
    et logo (repli ``img.product-brand`` : ``alt`` → marque, PNG de l'``onerror``)
  - ``a.js-product__image-link`` (galerie, multi) → images ; repli
    ``link[itemprop=image]`` / ``og:image``
  - ``meta[itemprop=description]`` / ``og:description`` → description
  - ``table#characteristicsTable`` → attributs (+ conditionnement, réf fabricant)
  - ``span.code_ean`` → EAN ; ``div.c-price--rep`` → éco-taxe (authentifié)
  - liens ``…​.pdf`` (directs ou ``javascript:window.open``) → documents
  - JSON-LD ``BreadcrumbList`` → fil d'Ariane

**Articles (déclinaisons)** : la fiche embarque la liste **JSON** de tous ses
articles dans l'attribut Stimulus ``data-pages--product-articles-value``, servie
**même sans session**. Chaque entrée porte ce qui est PROPRE à l'article — son
code, sa désignation, son image, ses axes de déclinaison (``caracsSpe``) et
surtout sa **référence fabricant** (``codeProvider``). ``fiche_et_articles``
émet un extrait par article ; ``articles_codes`` (table HTML) ne sert plus que
de garde-fou si l'attribut venait à disparaître.

⚠️ **La réf. fabricant du DOM est celle d'UN article, pas de la gamme.** La ligne
``tr#characCodeProvider`` de ``#characteristicsTable`` (rendue seulement en
authentifié) porte la référence de l'article surligné du tableau — qui n'est même
pas forcément celui de l'URL demandée. Mesuré le 20/08/2026 sur la gamme 16603 :
le DOM annonce ``G2F23010`` (article 588039) sur l'URL de l'article 782790, dont
la vraie référence fabricant est ``F23080``. Recopiée telle quelle sur les 12
articles, elle était donc fausse **partout, y compris sur l'article de la page**.
Elle ne sert plus qu'aux fiches mono-article, où elle est sans ambiguïté.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from core.texte import nettoyer_texte, normaliser_prix

from .legallais_sitemap import BASE_URL, ref_depuis_url

#: Attribut Stimulus portant la liste JSON des articles de la gamme.
ATTR_ARTICLES = "data-pages--product-articles-value"

#: États d'article publiés par Legallais. ``replaced`` = remplacé par un nouveau
#: modèle : la fiche reste consultable, l'article n'est plus commandable.
ETAT_PUBLIE = "published"
ETAT_REMPLACE = "replaced"

# Domaine du logo Legallais dans les og:image (à écarter : pas la photo produit).
_OG_IMAGE_LOGO = "og-legallais-logo"

# URL absolue d'un PDF, y compris dans un ``href="javascript:window.open('…')"``.
_PDF_RE = re.compile(r"https?://[^\s'\"()]+\.pdf", re.IGNORECASE)

# URL de repli d'un ``onerror`` JS (``this.src='…png'``).
_ONERROR_SRC_RE = re.compile(r"""src\s*=\s*['"](https?://[^'"]+)['"]""", re.IGNORECASE)

# Libellés de caractéristique portant le conditionnement / la réf fabricant.
_LABEL_CONDITIONNEMENT = ("unité de vente", "conditionnement", "vendu par")
_LABEL_REF_FABRICANT = ("référence fournisseur", "référence fabricant", "réf. fabricant")


def categories_depuis_breadcrumb(noms: list[str]) -> list[str]:
    """Catégories utiles d'un fil d'Ariane JSON-LD. **Pur**.

    Le fil Legallais commence par un ou plusieurs « Accueil » (racine dupliquée) :
    on les retire. Contrairement au fil DOM, la dernière entrée est ici une
    **catégorie** — le nom du produit n'apparaît pas dans le breadcrumb.
    """
    return [n.strip() for n in noms if n and n.strip().lower() != "accueil"]


def _texte_meta(soup: BeautifulSoup, criteres: dict[str, str]) -> str:
    """``content`` (ou ``href``) nettoyé de la 1ʳᵉ balise correspondante, ou ``""``."""
    balise = soup.find(attrs=criteres)
    if balise is None:
        return ""
    return nettoyer_texte(balise.get("content") or balise.get("href") or "")


def _categories(soup: BeautifulSoup) -> list[str]:
    """Fil d'Ariane depuis le premier JSON-LD ``BreadcrumbList`` de la page."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        # Legallais suffixe son JSON-LD d'un « ; » (``{…}];``) qui casse json.loads.
        contenu = (script.string or script.get_text() or "").strip().rstrip(";").strip()
        try:
            donnees = json.loads(contenu)
        except (ValueError, TypeError):
            continue
        if isinstance(donnees, dict) and donnees.get("@type") == "BreadcrumbList":
            noms = [
                nettoyer_texte(item.get("name"))
                for item in (donnees.get("itemListElement") or [])
                if isinstance(item, dict)
            ]
            return categories_depuis_breadcrumb(noms)
    return []


def _url_onerror(onerror: str) -> str:
    """URL d'image d'un ``onerror`` JS (``this.src='…'``), ou ``""``. **Pur**."""
    trouve = _ONERROR_SRC_RE.search(onerror or "")
    return trouve.group(1) if trouve else ""


def _image(soup: BeautifulSoup) -> str:
    """Photo produit : ``link[itemprop=image]`` d'abord, sinon og:image (hors logo)."""
    lien = _texte_meta(soup, {"itemprop": "image"})
    if lien:
        return lien
    for balise in soup.find_all("meta", attrs={"property": "og:image"}):
        url = nettoyer_texte(balise.get("content") or "")
        if url and _OG_IMAGE_LOGO not in url:
            return url
    return ""


def _images(soup: BeautifulSoup) -> list[str]:
    """Photos produit (valeurs multiples), dédupliquées dans l'ordre. **Pur**.

    Galerie ``a.js-product__image-link`` (le ``href`` porte la pleine taille) ;
    repli sur l'image unique quand la galerie est absente.
    """
    images: list[str] = []
    vus: set[str] = set()
    for lien in soup.select("a.js-product__image-link[href]"):
        url = nettoyer_texte(lien.get("href") or "")
        if url and url not in vus:
            vus.add(url)
            images.append(url)
    if images:
        return images
    unique = _image(soup)
    return [unique] if unique else []


def _designation(soup: BeautifulSoup) -> str:
    """Nom produit : og:title (fiable), repli sur le 1ᵉʳ ``meta[itemprop=name]``.

    Le ``meta[itemprop=name]`` de la marque est **imbriqué** dans
    ``[itemprop=brand]`` : ``find`` renvoie le premier du document, qui est le nom
    produit. og:title reste préféré car non ambigu.
    """
    return _texte_meta(soup, {"property": "og:title"}) or _texte_meta(soup, {"itemprop": "name"})


def _marque(soup: BeautifulSoup) -> str:
    """Marque : ``[itemprop=brand] [itemprop=name]``, repli ``img.product-brand[alt]``."""
    bloc = soup.find(attrs={"itemprop": "brand"})
    if bloc is not None:
        nom = bloc.find(attrs={"itemprop": "name"})
        marque = nettoyer_texte(nom.get("content") or nom.get_text()) if nom else ""
        if marque:
            return marque
    img = soup.select_one("img.product-brand[alt]")
    return nettoyer_texte(img.get("alt")) if img else ""


def _description(soup: BeautifulSoup) -> str:
    """Description : ``meta[itemprop=description]``, repli ``og:description``."""
    return _texte_meta(soup, {"itemprop": "description"}) or _texte_meta(
        soup, {"property": "og:description"}
    )


def _marque_logo(soup: BeautifulSoup) -> str:
    """Logo de marque : ``a.u-display-contents img``, repli ``img.product-brand``.

    Sur ces dernières fiches le ``src`` est un SVG qui ne se télécharge pas
    toujours ; l'``onerror`` fournit le PNG de repli — c'est lui qu'on retient.
    """
    img = soup.select_one("a.u-display-contents img")
    if img and img.get("src"):
        return nettoyer_texte(img.get("src"))
    img = soup.select_one("img.product-brand")
    if img is None:
        return ""
    return _url_onerror(img.get("onerror") or "") or nettoyer_texte(img.get("src") or "")


def _ean(soup: BeautifulSoup) -> str:
    """Code EAN (``span.code_ean span.code_ean_value``) — souvent absent."""
    span = soup.select_one("span.code_ean span.code_ean_value")
    return nettoyer_texte(span.get_text()) if span else ""


def _eco_taxe(soup: BeautifulSoup) -> str:
    """Éco-participation (``div.c-price--rep``) normalisée, ou ``""``."""
    element = soup.select_one("div.c-price.c-price--rep div.c-price__price")
    return normaliser_prix(element.get_text()) if element else ""


def _attributs(soup: BeautifulSoup) -> dict[str, str]:
    """``{label: valeur}`` depuis ``#characteristicsTable``.

    Chaque ligne est une paire ``<th>label</th><td>valeur</td>``. Vide si la fiche
    ne rend pas sa table (gamme non authentifiée).
    """
    attributs: dict[str, str] = {}
    for ligne in soup.select("table#characteristicsTable tr"):
        label = ligne.find("th")
        valeur = ligne.find("td")
        if label is None or valeur is None:
            continue
        cle = nettoyer_texte(label.get_text())
        val = nettoyer_texte(valeur.get_text())
        if cle and val and cle not in attributs:
            attributs[cle] = val
    return attributs


def attributs_du_tableau(html: str) -> dict[str, str]:
    """Caractéristiques ``{label: valeur}`` d'une fiche (HTML → dict). **Pur**."""
    return _attributs(BeautifulSoup(html, "html.parser"))


def _valeur_par_label(attributs: dict[str, str], libelles: tuple[str, ...]) -> str:
    """Valeur d'une caractéristique dont le label contient l'un des ``libelles``. **Pur**."""
    for cle, valeur in attributs.items():
        bas = cle.lower()
        if any(libelle in bas for libelle in libelles):
            return valeur
    return ""


def _docs(soup: BeautifulSoup) -> list[str]:
    """URLs des fiches techniques PDF (href direct ou ``javascript:window.open``)."""
    urls: list[str] = []
    vus: set[str] = set()
    for lien in soup.find_all("a", href=True):
        for pdf in _PDF_RE.findall(lien["href"]):
            if pdf not in vus:
                vus.add(pdf)
                urls.append(pdf)
    return urls


def parser_fiche(html: str, url: str) -> dict:
    """HTML statique + URL → extrait prêt pour ``core.f2.element_produit``. **Pur**.

    ``prix`` et ``stock`` restent vides ici : ils viennent de l'enrichissement.
    La référence est l'id de page.
    """
    return _fiche(BeautifulSoup(html, "html.parser"), url)


def _fiche(soup: BeautifulSoup, url: str) -> dict:
    """Base **page** (niveau gamme) depuis une soupe déjà construite. **Pur**."""
    attributs = _attributs(soup)
    return {
        "url": url,
        "ref": ref_depuis_url(url),
        "designation": _designation(soup),
        "prix": "",
        "eco": _eco_taxe(soup),
        "stock": "",
        "marque": _marque(soup),
        "marque_logo": _marque_logo(soup),
        "ean": _ean(soup),
        "ref_fabricant": _valeur_par_label(attributs, _LABEL_REF_FABRICANT),
        "description": _description(soup),
        "conditionnement": _valeur_par_label(attributs, _LABEL_CONDITIONNEMENT),
        "categories": _categories(soup),
        "images": _images(soup),
        "docs": _docs(soup),
        "attributs": attributs,
    }


def _articles_json(soup: BeautifulSoup) -> list[dict]:
    """Liste JSON des articles de la gamme, ou ``[]``. **Pur**."""
    balise = soup.find(attrs={ATTR_ARTICLES: True})
    if balise is None:
        return []
    try:
        articles = json.loads(balise.get(ATTR_ARTICLES) or "")
    except (ValueError, TypeError):
        return []
    if not isinstance(articles, list):
        return []
    return [a for a in articles if isinstance(a, dict) and a.get("code")]


def url_article(url_page: str, code: str) -> str:
    """URL de page + code article → URL de l'article. **Pur**.

    C'est ce qui donne à chaque déclinaison une identité ``product_uid`` distincte
    (l'identité Legallais est l'URL, cf. ``core.dedup.CRITERES_PAR_SITE``) : sans
    elle, les articles d'une même gamme s'écrasent mutuellement en base.

    Le sitemap énumère des URL de **gamme** (``/produit/<slug>/<id>``) : on ajoute
    alors le code. Une URL d'**article** (``…/<gamme>/<code>``) voit son dernier
    segment remplacé, jamais empilé.
    """
    if not code:
        return url_page
    morceaux = urlsplit(url_page)
    segments = [s for s in morceaux.path.split("/") if s]
    if len(segments) >= 4 and segments[-1].isdigit() and segments[-2].isdigit():
        segments[-1] = code
    else:
        segments.append(code)
    return urlunsplit((morceaux.scheme, morceaux.netloc, "/" + "/".join(segments), "", ""))


def _url_article(article: dict, url_page: str) -> str:
    """URL de l'article : ``link`` de la gamme + code, repli sur l'URL de page. **Pur**."""
    lien = article.get("link") or ""
    if not lien:
        return url_article(url_page, article["code"])
    base = lien if lien.startswith("http") else f"{BASE_URL}{lien}"
    return f"{base.rstrip('/')}/{article['code']}"


def _url_media(chemin: object) -> str:
    """Chemin de média → URL absolue, ``""`` si vide. **Pur**."""
    propre = nettoyer_texte(chemin)
    if not propre or propre.startswith("http"):
        return propre
    return f"{BASE_URL}{propre if propre.startswith('/') else '/' + propre}"


def _categories_article(article: dict) -> list[str]:
    """``universe > family > subFamily`` de l'article. **Pur**."""
    arbre = article.get("categories")
    if not isinstance(arbre, dict):
        return []
    niveaux = (arbre.get("universe"), arbre.get("family"), arbre.get("subFamily"))
    return [
        nettoyer_texte(n.get("title"))
        for n in niveaux
        if isinstance(n, dict) and nettoyer_texte(n.get("title"))
    ]


def _caracs(article: dict, cle: str) -> dict[str, str]:
    """``caracsCommune`` ou ``caracsSpe`` nettoyées. **Pur**."""
    brut = article.get(cle)
    if not isinstance(brut, dict):
        return {}
    propres = {}
    for label, valeur in brut.items():
        nom, val = nettoyer_texte(label), nettoyer_texte(valeur)
        if nom and val:
            propres[nom] = val
    return propres


def _article_extrait(article: dict, base: dict, url_page: str, *, seul: bool) -> dict:
    """Un article de la gamme → extrait ``core.f2.element_produit``. **Pur**.

    La base page fournit le fond (description, docs, conditionnement, éco-taxe) ;
    l'article a le dernier mot sur tout ce qui lui est propre.

    ``prix`` reste **volontairement vide** : le JSON porte ``base_price``, qui est
    le prix **public** quand la fiche est servie sans session. L'écrire ici ferait
    écraser, à la première passe nologin, le prix compte déjà en base. Le prix
    continue donc de venir du seul ``/get-article-infos`` (cf.
    ``legallais_article_infos``).
    """
    axes = _caracs(article, "caracsSpe")
    extrait = dict(base)
    extrait.update({
        "url": _url_article(article, url_page),
        "ref": str(article["code"]),
        "ref_fabricant": nettoyer_texte(article.get("codeProvider")),
        "designation": nettoyer_texte(article.get("title")) or base.get("designation", ""),
        "prix": "",
        "marque": nettoyer_texte(article.get("brandTitle")) or base.get("marque", ""),
        "categories": _categories_article(article) or base.get("categories", []),
        "attributs": {**base.get("attributs", {}), **_caracs(article, "caracsCommune"), **axes},
        # Toujours renseigné, jamais vide : ``save_product`` ne remplace pas une
        # valeur en base par du vide, donc un « replaced » ne s'effacerait plus
        # jamais si l'article redevenait publié.
        "statut": nettoyer_texte(article.get("state")) or ETAT_PUBLIE,
    })
    logo = _url_media(article.get("brandLogo"))
    if logo:
        extrait["marque_logo"] = logo
    image = _url_media(article.get("imageUrl"))
    if image:
        extrait["images"] = [image]
    if not seul:
        # ⚠️ L'EAN du DOM est celui de la page, donc d'UN article : le recopier sur
        # une gamme de 12 déclinaisons donnerait 12 articles au même code-barres,
        # et l'EAN est une clé de rapprochement côté PIM. Le JSON n'en fournit pas
        # par article → on préfère aucun EAN à un EAN faux.
        extrait["ean"] = ""
    return extrait


def fiche_et_articles(html: str, url: str) -> tuple[dict, list[dict]]:
    """HTML → ``(base page, un extrait par article)``, en **un seul** parsing. **Pur**.

    Liste d'articles vide quand la fiche n'en publie aucun (gamme dont la
    commercialisation est arrêtée, ou attribut absent) : l'appelant émet alors la
    base page seule. Un parsing unique, car la fiche pèse ~800 Ko et la voie
    sitemap en traite ~48 000.
    """
    soup = BeautifulSoup(html, "html.parser")
    base = _fiche(soup, url)
    articles = _articles_json(soup)
    seul = len(articles) == 1
    return base, [_article_extrait(a, base, url, seul=seul) for a in articles]


def articles_codes(html: str) -> list[str]:
    """Codes article de la table de références (dédupliqués, ordre). **Pur**.

    Ne cible que les ``<tr>`` portant ``data-article-code`` (la ligne d'en-tête
    n'en a pas → naturellement exclue). Vide si la fiche ne rend pas ses lignes en
    statique, ou si la requête n'était pas authentifiée.
    """
    soup = BeautifulSoup(html, "html.parser")
    codes: list[str] = []
    vus: set[str] = set()
    for ligne in soup.select("tr.c-references-articles__table__line[data-article-code]"):
        code = (ligne.get("data-article-code") or "").strip()
        if code and code not in vus:
            vus.add(code)
            codes.append(code)
    return codes
