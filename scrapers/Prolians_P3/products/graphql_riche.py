"""
Client GraphQL Prolians **fiche riche** — version async pour la méthode « api ».

Différence avec ``prolians_graphql.py`` (qui reste en place, en Playwright
**sync**, pour ``scrap_prolians_light`` et ``scrap_prolians_price_stock``) :

1. Il est **async**, donc utilisable par le socle ``core/scrap_base.py``.
2. Il ajoute la **requête riche** : description, images, caractéristiques,
   éco-participation, référence fabricant, conditionnement.

Pourquoi c'est décisif
----------------------
Les opérations capturées sur une page de listing (``ProductListPriceAndStock``,
``VisibleCardsAnalyticsByReferences``) ne rendent que prix / stock / marque /
nom / fil d'Ariane. Le reste — description, images, attributs, éco-part —
demandait jusqu'ici de **rendre une page par fiche** : ~14 fiches/min, soit plus
de cent heures pour le catalogue.

Or ces champs sont dans le schéma, sur le **même** ``productsByReferences`` déjà
batché par 100. Il suffisait de les demander : la réponse est complète, **en
anonyme et sans navigateur**. C'est ce que fait ``REQUETE_FICHE_RICHE``.

Deux moitiés nettes :
  - **pure / testable** — mapping produit GraphQL → colonnes ``product_*``
  - **live** — ``EnrichisseurGraphQL`` capture la session authentifiée depuis une
    requête interceptée après login, puis la rejoue par lots.

⚠️ Absents du schéma : ``brandLogo`` et ``breadcrumb`` (portés par la page, pas
par le produit) — le fil d'Ariane vient donc de l'op *analytics*. Et **l'EAN
n'existe pas** : ``ean``/``gtin``/``barcode`` sont tous refusés.

⚠️ Pas de déclinaisons : ``parentReference`` est délibérément ignoré (décision
06/08/2026, tout est traité comme article simple).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

_log = logging.getLogger(__name__)

BASE_URL = "https://www.prolians.fr"
API_URL = "https://api.prolians.fr/graphql"

# Opérations déclenchées au chargement d'une page de listing (capturées, pas écrites).
OP_PRIX_STOCK = "ProductListPriceAndStock"  # prix, stock, dispo, conditionnement
OP_ANALYTICS = "VisibleCardsAnalyticsByReferences"  # nom, marque, fil d'Ariane

# Page de listing servant à amorcer (capture des en-têtes authentifiés + queries).
URL_AMORCAGE = (
    f"{BASE_URL}/nos-produits/outillage/outillage-a-main/"
    "tournevis-cles-males-et-embouts-de-vissage/compositions-et-trousses-tournevis"
)

#: Références par appel GraphQL (l'API est batchable).
TAILLE_LOT = 100

#: Défilements à l'amorçage : le listing charge ses cartes en lazy-load et ne
#: déclenche les opérations prix/stock qu'au scroll.
NB_SCROLLS_AMORCAGE = 4

OP_FICHE_RICHE = "FicheRicheParReferences"
REQUETE_FICHE_RICHE = """
query FicheRicheParReferences($references: [String!]!) {
  productsByReferences(references: $references) {
    reference
    name
    urlKey
    description
    shortDescription
    supplierReference
    brand { name }
    images { alt set { url media } }
    documents { __typename }
    mainTechnicalSpecs { label value }
    technicalSpecs { label value }
    ecoPart { deee pmcb furnishing }
    packaging { quantity unit incrementQuantity minimalSalesQuantity }
  }
}
"""

#: La requête riche ne demande **aucune authentification** (validé en live) : elle
#: survit donc à une session Prolians expirée, contrairement aux prix.
ENTETES_ANONYMES = {
    "content-type": "application/json",
    "accept": "*/*",
    "x-client": "prolians-web",
    "x-distribution-network": "prolians",
    "x-environment": "production",
}

#: Variante d'image retenue quand la fiche en propose plusieurs : la même URL est
#: servie pour DESKTOP et MOBILE dans les cas observés, mais on fixe le choix pour
#: que la sortie soit déterministe.
MEDIA_PREFERE = "DESKTOP"

SEP_CATEGORIE = " > "
SEP_LISTE = "||"


# ═══════════════════════════════════════════════════════════════════════════════
# MAPPING (pur, testable)
# ═══════════════════════════════════════════════════════════════════════════════

def statut_stock(produit: dict) -> str:
    """Statut de stock déduit de la disponibilité GraphQL. **Pur**."""
    if produit.get("isSellable") is False:
        return "non disponible"
    return "disponible" if (produit.get("availability") or []) else "non disponible"


def fil_ariane(produit: dict) -> str:
    """Fil d'Ariane depuis les *breadcrumbs* GraphQL. **Pur**.

    Les breadcrumbs sont ``[Produits, cat1, …, catN, NomDuProduit]`` : on retire
    la racine « Produits » et le dernier élément (le produit lui-même).
    """
    noms = [c.get("name") for c in (produit.get("breadcrumbs") or []) if c.get("name")]
    cats = [n for n in noms if n != "Produits"]
    if cats:
        cats = cats[:-1]
    return SEP_CATEGORIE.join(cats)


def images_produit(produit: dict) -> list[str]:
    """URLs d'images, dédupliquées, dans l'ordre de la fiche. **Pur**.

    Forme réelle : ``images: [{alt, set: [{url, media}]}]`` — un *set* par visuel,
    une entrée par variante d'affichage. On préfère ``MEDIA_PREFERE`` et on
    retombe sur la première URL disponible, pour ne jamais perdre un visuel qui
    n'aurait pas la variante.
    """
    urls: list[str] = []
    vues: set[str] = set()
    for image in produit.get("images") or []:
        variantes = (image or {}).get("set") or []
        choisie = next(
            (v.get("url") for v in variantes if v.get("media") == MEDIA_PREFERE and v.get("url")),
            next((v.get("url") for v in variantes if v.get("url")), ""),
        )
        if choisie and choisie not in vues:
            vues.add(choisie)
            urls.append(choisie)
    return urls


def attributs_produit(produit: dict) -> dict[str, str]:
    """Caractéristiques techniques → ``{libellé: valeur}``. **Pur**.

    ``technicalSpecs`` est la liste complète, ``mainTechnicalSpecs`` un
    sous-ensemble mis en avant : on part de la complète et on complète avec
    l'autre sans écraser, au cas où une fiche ne remplirait que la seconde.
    """
    attributs: dict[str, str] = {}
    for cle in ("technicalSpecs", "mainTechnicalSpecs"):
        for spec in produit.get(cle) or []:
            libelle = (spec or {}).get("label")
            valeur = (spec or {}).get("value")
            if libelle and valeur not in (None, "") and libelle not in attributs:
                attributs[libelle] = str(valeur)
    return attributs


def eco_participation(produit: dict) -> str:
    """Éco-participation totale (DEEE + PMCB + mobilier), ``""`` si aucune. **Pur**.

    L'API éclate l'éco-contribution en trois filières là où la colonne n'en porte
    qu'une : on somme les filières renseignées. Aucune renseignée → chaîne vide
    (et non « 0 »), pour ne pas affirmer une absence d'éco-taxe qui serait en
    réalité une donnée manquante.
    """
    parts = produit.get("ecoPart")
    if not isinstance(parts, dict):
        return ""
    total = 0.0
    trouve = False
    for filiere in ("deee", "pmcb", "furnishing"):
        valeur = parts.get(filiere)
        if valeur in (None, ""):
            continue
        try:
            total += float(valeur)
            trouve = True
        except (TypeError, ValueError):
            continue
    return f"{total:.2f}" if trouve else ""


def conditionnement(produit: dict) -> str:
    """Quantité de conditionnement, ``""`` si absente. **Pur**."""
    pack = produit.get("packaging")
    if isinstance(pack, dict):
        pack = pack.get("quantity") or pack.get("value") or ""
    return "" if pack in (None, "") else str(pack)


def champs_fiche_riche(produit: dict) -> dict:
    """Champs que seule la requête riche apporte. **Pur**.

    Séparé de ``champs_enrichissement`` parce que les deux requêtes ont des
    exigences opposées : celle-ci marche en anonyme, celle des prix **exige** la
    session. Les garder distincts permet d'obtenir la fiche complète même quand
    la session est morte.

    ⚠️ ``urlKey`` n'est **pas** utilisé pour réécrire ``product_fournisseur_url`` :
    l'URL qui identifie la fiche est celle par laquelle on l'a énumérée (sitemap),
    et c'est elle qui porte l'identité ``product_uid``. La remplacer ferait
    apparaître la même fiche sous deux identités.
    """
    champs: dict = {}
    description = produit.get("description") or produit.get("shortDescription")
    if description:
        champs["product_description"] = description
    images = images_produit(produit)
    if images:
        champs["product_image_url"] = SEP_LISTE.join(images)
    attributs = attributs_produit(produit)
    if attributs:
        champs["product_attributes"] = json.dumps(attributs, ensure_ascii=False)
    eco = eco_participation(produit)
    if eco:
        champs["product_eco_taxe"] = eco
    fabricant = produit.get("supplierReference")
    if fabricant:
        champs["product_reference_fabricant"] = str(fabricant)
    pack = conditionnement(produit)
    if pack:
        champs["product_conditionnement"] = pack
    return champs


def champs_enrichissement(produit: dict) -> dict:
    """Champs prix / stock / marque / catégories (ops capturées). **Pur**.

    Ne renvoie que ce qui est réellement présent : les valeurs absentes sont
    omises pour que ``save_product`` n'écrase pas la base sitemap avec des vides.
    ``parentReference`` est volontairement ignoré (pas de déclinaisons).
    """
    champs: dict = {}
    prix = produit.get("price") or {}
    hors_taxe = prix.get("exclTax")
    if hors_taxe is not None:
        champs["product_price_ht"] = str(hors_taxe)
    champs["product_stock_status"] = statut_stock(produit)
    pack = conditionnement(produit)
    if pack:
        champs["product_conditionnement"] = pack
    remise = prix.get("discountPercentage")
    if remise:
        champs["product_promotion"] = str(remise)
    if produit.get("name"):
        champs["product_designation"] = produit["name"]
    marque = produit.get("brand") or {}
    if marque.get("name"):
        champs["product_brand"] = marque["name"]
    fil = fil_ariane(produit)
    if fil:
        champs["product_category_tree"] = fil
    return champs


def _entetes_contexte(bruts: dict) -> dict:
    """En-têtes à rejouer : ``Authorization`` + ``x-*`` (hors ``x-forwarded-for``). **Pur**.

    On ne transmet que le jeton et les en-têtes de contexte métier, jamais ceux
    du transport navigateur (``host``, ``cookie``, ``sec-*``, ``user-agent``…).
    """
    entetes = {"content-type": "application/json"}
    for cle, valeur in bruts.items():
        bas = cle.lower()
        if bas == "authorization" or (bas.startswith("x-") and bas != "x-forwarded-for"):
            entetes[cle] = valeur
    return entetes


def _operation_capturable(reponse) -> tuple[str, dict] | None:
    """``(op, {query, entetes})`` si la réponse est une op GraphQL ciblée, sinon ``None``."""
    requete = reponse.request
    if requete.method != "POST" or "graphql" not in reponse.url.lower():
        return None
    corps = requete.post_data or ""
    if not corps:
        return None
    charge = json.loads(corps)
    op = charge.get("operationName")
    if op not in (OP_PRIX_STOCK, OP_ANALYTICS):
        return None
    return op, {"query": charge["query"], "entetes": dict(requete.headers)}


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE
# ═══════════════════════════════════════════════════════════════════════════════

class EnrichisseurGraphQL:
    """Accès batché à ``productsByReferences`` réutilisant la session navigateur.

    ``amorcer(page)`` — une fois, après login — capture en-têtes authentifiés et
    queries depuis une vraie requête interceptée ; ``recuperer(contexte, refs)``
    les rejoue par lots de 100.
    """

    def __init__(self) -> None:
        self._entetes: dict[str, str] | None = None
        self._queries: dict[str, str] = {}

    @property
    def pret(self) -> bool:
        """Vrai si l'amorçage a capturé au moins l'opération prix/stock."""
        return bool(self._entetes and OP_PRIX_STOCK in self._queries)

    async def amorcer(self, page: Page) -> bool:
        """Charge la page d'amorçage et capture en-têtes + queries GraphQL."""
        captures = await self._capturer(page)
        if OP_PRIX_STOCK not in captures:
            _log.error("Amorçage GraphQL échoué — opération %s non capturée", OP_PRIX_STOCK)
            return False
        self._entetes = _entetes_contexte(captures[OP_PRIX_STOCK]["entetes"])
        self._queries[OP_PRIX_STOCK] = captures[OP_PRIX_STOCK]["query"]
        if OP_ANALYTICS in captures:
            self._queries[OP_ANALYTICS] = captures[OP_ANALYTICS]["query"]
        _log.info("Amorçage GraphQL OK (ops=%s)", list(self._queries))
        return True

    async def _capturer(self, page: Page) -> dict[str, dict]:
        """Charge la page d'amorçage en interceptant les requêtes GraphQL ciblées."""
        captures: dict[str, dict] = {}

        def sur_reponse(reponse) -> None:
            try:
                capture = _operation_capturable(reponse)
            except Exception:  # capture best-effort : une réponse illisible n'arrête rien
                return
            if capture and capture[0] not in captures:
                captures[capture[0]] = capture[1]

        page.on("response", sur_reponse)
        try:
            # ⚠️ Pas de ``networkidle`` : le site poll en continu (tracking), l'état
            # n'est jamais atteint et la capture est ratée. On charge le DOM puis on
            # scrolle pour déclencher le lazy-load des cartes.
            await page.goto(URL_AMORCAGE, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(3_000)
            for _ in range(NB_SCROLLS_AMORCAGE):
                if OP_PRIX_STOCK in captures and OP_ANALYTICS in captures:
                    break
                await page.mouse.wheel(0, 4_000)
                await page.wait_for_timeout(1_200)
        except Exception as exc:
            _log.warning("Amorçage GraphQL — navigation échouée : %s", exc)
        finally:
            try:
                page.remove_listener("response", sur_reponse)
            except Exception:
                pass
        return captures

    async def _appeler(self, contexte: BrowserContext, op: str, refs: list[str]) -> list[dict]:
        """Un appel d'une opération **capturée** pour un lot de références."""
        if op not in self._queries:
            return []
        try:
            reponse = await contexte.request.post(
                API_URL,
                data=json.dumps({
                    "operationName": op,
                    "variables": {"references": refs},
                    "query": self._queries[op],
                }),
                headers=self._entetes,
            )
            donnees = await reponse.json()
        except Exception as exc:
            _log.warning("Appel GraphQL %s échoué : %s", op, exc)
            return []
        if not isinstance(donnees, dict):
            return []
        if donnees.get("errors"):
            _log.warning("GraphQL %s erreurs : %s", op, json.dumps(donnees["errors"])[:200])
        return (donnees.get("data") or {}).get("productsByReferences") or []

    async def _appeler_fiche_riche(self, contexte: BrowserContext, refs: list[str]) -> list[dict]:
        """Un appel de la requête **riche** — en anonyme, sans la session."""
        try:
            reponse = await contexte.request.post(
                API_URL,
                data=json.dumps({
                    "operationName": OP_FICHE_RICHE,
                    "variables": {"references": refs},
                    "query": REQUETE_FICHE_RICHE,
                }),
                headers=ENTETES_ANONYMES,
            )
            donnees = await reponse.json()
        except Exception as exc:  # un lot en erreur ne doit pas tuer le run
            _log.warning("Requête riche Prolians en erreur (%d réfs) : %s", len(refs), exc)
            return []
        if not isinstance(donnees, dict):
            return []
        if donnees.get("errors"):
            # Rendre le refus VISIBLE : un champ retiré du schéma par Prolians ferait
            # silencieusement retomber la fiche à « prix seul », sans que rien n'alerte.
            messages = "; ".join(
                str((e or {}).get("message", ""))[:120] for e in donnees["errors"][:3]
            )
            _log.warning("Requête riche Prolians refusée par l'API : %s", messages)
        return (donnees.get("data") or {}).get("productsByReferences") or []

    async def recuperer(self, contexte: BrowserContext, refs: list[str], *,
                        avec_analytics: bool = True,
                        avec_fiche_riche: bool = True) -> dict[str, dict]:
        """Récupère et fusionne les données produit pour ``refs`` (lots de 100).

        La requête riche est appelée **en premier** : les ops capturées
        (authentifiées) écrasent donc ses valeurs communes — prix et stock
        viennent toujours de la session.
        """
        ops = (OP_PRIX_STOCK, OP_ANALYTICS) if avec_analytics else (OP_PRIX_STOCK,)
        fusion: dict[str, dict] = {}
        for depart in range(0, len(refs), TAILLE_LOT):
            tranche = refs[depart:depart + TAILLE_LOT]
            sources = []
            if avec_fiche_riche:
                sources.append(await self._appeler_fiche_riche(contexte, tranche))
            for op in ops:
                sources.append(await self._appeler(contexte, op, tranche))
            for produits in sources:
                for produit in produits:
                    ref = str(produit.get("reference") or "")
                    if ref:
                        fusion.setdefault(ref, {}).update(produit)
        return fusion
