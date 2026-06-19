"""
Client GraphQL Prolians — accès direct à l'API ``api.prolians.fr/graphql``.

Le site est une app Next.js/RSC : la liste des produits d'une catégorie est
rendue côté serveur (pas de requête GraphQL « produits par catégorie » publique).
En revanche l'opération ``productsByReferences`` est publique et **batchable** :
on lui passe une liste de références (SKU 8 chiffres) et elle renvoie en une
seule requête le prix, le stock, la disponibilité, le conditionnement, la marque,
le nom et le fil d'Ariane (catégories) — ~250× plus rapide que le scraping DOM.

L'API exige des en-têtes de contexte (société, point de vente, réseau de
distribution, jeton Bearer) que l'on **capture depuis le navigateur authentifié**
via ``prime()`` (on intercepte une vraie requête après login). Les chaînes de
requête GraphQL sont elles aussi capturées telles quelles, ce qui garantit des
formes de champs valides sans dépendre de l'introspection (désactivée côté API).

Limites : ne renvoie PAS description, images, documents, caractéristiques, EAN,
éco-taxe/label, cross-sell ni le détail des déclinaisons → pour une fiche
complète il faut toujours le moteur DOM ``scraper_prolians_products``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from css_selectors.prolians import Selectors
from core.logger import setup_logger

log = setup_logger("prolians.graphql")

API_URL = "https://api.prolians.fr/graphql"

# Opérations à capturer sur une page de listing (déclenchées au chargement)
OP_PRICE_STOCK = "ProductListPriceAndStock"   # prix, stock, dispo, conditionnement
OP_ANALYTICS   = "VisibleCardsAnalyticsByReferences"  # nom, marque, fil d'Ariane

# Page de listing utilisée pour « amorcer » (capturer requêtes + en-têtes)
PRIME_URL = (
    f"{Selectors.BASE_URL}/nos-produits/outillage/outillage-a-main/"
    "tournevis-cles-males-et-embouts-de-vissage/compositions-et-trousses-tournevis"
)

BATCH_SIZE = 100  # nb de références par requête


class ProliansGraphQL:
    """Accès batché à ``productsByReferences`` réutilisant la session navigateur."""

    def __init__(self) -> None:
        self._headers: dict | None = None
        self._queries: dict[str, str] = {}

    # ─── Amorçage : capture en-têtes + requêtes depuis une vraie page ─────────

    def prime(self, page) -> bool:
        """Charge une page de listing et capture en-têtes + requêtes GraphQL.

        À appeler une fois après le login (page déjà authentifiée). Retourne
        True si au moins l'opération prix/stock a été capturée.
        """
        captured: dict[str, dict] = {}

        def on_resp(resp):
            try:
                req = resp.request
                if req.method != "POST" or "graphql" not in resp.url.lower():
                    return
                pd = req.post_data or ""
                if not pd:
                    return
                j = json.loads(pd)
                op = j.get("operationName")
                if op in (OP_PRICE_STOCK, OP_ANALYTICS) and op not in captured:
                    captured[op] = {"query": j["query"], "headers": dict(req.headers)}
            except Exception:
                pass

        page.on("response", on_resp)
        try:
            page.goto(PRIME_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2500)
        except Exception as exc:
            log.warning("Amorçage GraphQL — navigation échouée : %s", exc)
        finally:
            try:
                page.remove_listener("response", on_resp)
            except Exception:
                pass

        if OP_PRICE_STOCK not in captured:
            log.error("Amorçage GraphQL échoué — requête %s non capturée", OP_PRICE_STOCK)
            return False

        # En-têtes de contexte : on transmet tous les x-* + authorization
        raw = captured[OP_PRICE_STOCK]["headers"]
        headers = {"content-type": "application/json"}
        for k, v in raw.items():
            kl = k.lower()
            if kl == "authorization" or (kl.startswith("x-") and kl != "x-forwarded-for"):
                headers[k] = v
        self._headers = headers
        self._queries[OP_PRICE_STOCK] = captured[OP_PRICE_STOCK]["query"]
        if OP_ANALYTICS in captured:
            self._queries[OP_ANALYTICS] = captured[OP_ANALYTICS]["query"]
        has_auth = any(k.lower() == "authorization" for k in headers)
        log.info("Amorçage GraphQL OK (auth=%s, ops=%s)", has_auth, list(self._queries))
        return True

    @property
    def ready(self) -> bool:
        return bool(self._headers and OP_PRICE_STOCK in self._queries)

    @property
    def has_analytics(self) -> bool:
        return OP_ANALYTICS in self._queries

    # ─── Appels batchés ───────────────────────────────────────────────────────

    def _post(self, context, op: str, refs: list[str]) -> list[dict]:
        """Un appel ``productsByReferences`` pour un lot de références."""
        if op not in self._queries:
            return []
        try:
            resp = context.request.post(
                API_URL,
                data=json.dumps({
                    "operationName": op,
                    "variables": {"references": refs},
                    "query": self._queries[op],
                }),
                headers=self._headers,
            )
            data = resp.json()
        except Exception as exc:
            log.warning("Appel GraphQL %s échoué : %s", op, exc)
            return []
        if isinstance(data, dict) and data.get("errors"):
            log.warning("GraphQL %s erreurs : %s", op, json.dumps(data["errors"])[:200])
        return ((data.get("data") or {}).get("productsByReferences") or []) if isinstance(data, dict) else []

    def fetch(self, context, refs: list[str], with_analytics: bool = False) -> dict[str, dict]:
        """Récupère les données produit pour ``refs``, fusionnées par référence.

        Retourne ``{reference: {champs prix/stock (+ nom/marque/catégorie)}}``.
        """
        merged: dict[str, dict] = {}
        for i in range(0, len(refs), BATCH_SIZE):
            chunk = refs[i:i + BATCH_SIZE]
            for p in self._post(context, OP_PRICE_STOCK, chunk):
                ref = str(p.get("reference") or "")
                if ref:
                    merged.setdefault(ref, {}).update(p)
            if with_analytics and self.has_analytics:
                for p in self._post(context, OP_ANALYTICS, chunk):
                    ref = str(p.get("reference") or "")
                    if ref:
                        merged.setdefault(ref, {}).update(p)
        return merged


# ─── Mapping GraphQL → colonnes CSV ───────────────────────────────────────────

def _stock_status(p: dict) -> str:
    """Déduit le statut de stock depuis la disponibilité GraphQL."""
    if p.get("isSellable") is False:
        return "non disponible"
    avail = p.get("availability") or []
    return "disponible" if avail else "non disponible"


def price_stock_fields(p: dict) -> dict:
    """Champs prix/stock (pour une MAJ ciblée)."""
    fields: dict = {}
    price = p.get("price") or {}
    excl = price.get("exclTax")
    if excl is not None:
        fields["product_price_ht"] = str(excl)
    fields["product_stock_status"] = _stock_status(p)
    pack = p.get("packaging")
    if isinstance(pack, dict):
        pack = pack.get("quantity") or pack.get("value") or ""
    if pack not in (None, ""):
        fields["product_conditionnement"] = str(pack)
    disc = price.get("discountPercentage")
    if disc:
        fields["product_promotion"] = str(disc)
    return fields


def _category_tree(p: dict) -> str:
    """Construit le fil d'Ariane catégories.

    Les breadcrumbs GraphQL sont [Produits, cat1, …, catN, NomDuProduit] : on
    retire « Produits » (racine) et le dernier élément (= le produit lui-même).
    """
    names = [c.get("name") for c in (p.get("breadcrumbs") or []) if c.get("name")]
    cats = [n for n in names if n != "Produits"]
    if cats:
        cats = cats[:-1]  # enlève le nom du produit en fin de fil
    return "||".join(cats)


def light_row(p: dict) -> dict:
    """Ligne « catalogue léger » complète (champs disponibles via GraphQL)."""
    row = price_stock_fields(p)
    row["product_fournisseur"] = "P3"
    ref = str(p.get("reference") or "")
    row["product_reference_fournisseur"] = ref
    parent = str(p.get("parentReference") or "")
    if parent:
        row["product_parent_reference"] = parent
    if p.get("name"):
        row["product_designation"] = p["name"]
    brand = p.get("brand") or {}
    if brand.get("name"):
        row["product_brand"] = brand["name"]
    tree = _category_tree(p)
    if tree:
        row["product_category_tree"] = tree
    url_key = p.get("urlKey")
    if url_key:
        row["product_fournisseur_url"] = f"{Selectors.BASE_URL}/{url_key}"
    return row
