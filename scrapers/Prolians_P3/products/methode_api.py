"""
Méthode « api » — catalogue Prolians par sitemap + enrichissement GraphQL batché.

Voie de production recommandée pour P3 :

1. **Énumération** — le sitemap liste tout le catalogue en HTTP simple, sans
   login (``prolians_sitemap.iter_product_entries``) : une ligne de base par
   produit (URL, nom, images, réf déduite de l'URL).
2. **Enrichissement par lots de 100** — ``graphql_riche.EnrichisseurGraphQL``
   ajoute prix, stock, marque, fil d'Ariane (session requise) **et** description,
   images, caractéristiques, éco-participation, référence fabricant
   (requête riche, anonyme).
3. **Écriture** — chaque lot part par ``emettre_donnee('produits', …)`` vers le
   sink, donc ``save_product`` : aucune fiche dupliquée, aucun champ écrasé par
   du vide.

Ordre de grandeur : le DOM rendait ~14 fiches/min (une page par fiche). Ici un
appel batché couvre 100 références.

Deux silences à éviter, appris en production :

- **Le battement entoure TOUT le run**, amorçage inclus. L'amorçage ouvre un
  navigateur et peut enchaîner un auto-login ; l'énumération du sitemap descend
  robots.txt → index → sous-sitemaps → des dizaines de milliers d'URLs. Sans
  battement, des minutes entières sans le moindre signe de vie.
- **L'énumération tourne en thread** : elle fait des fetchs ``urllib``
  synchrones qui gèleraient la boucle async — et donc le battement.

Et surtout : **pas de dégradation silencieuse**. Si l'enrichissement demandé
échoue, on lève. Un catalogue émis sans prix ni stock avec un gros compteur
ressemble à un succès ; c'est un échec.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from core.f2 import element_produit
from core.garde_session import message_progression
from core.login_auto import login_auto
from core.scrap_base import PlaywrightScraper, ScrapeAnnule
from core.sessions import GestionnaireSessions

from . import graphql_riche, prolians_sitemap

_log = logging.getLogger(__name__)

FOURNISSEUR = "P3"
FOURNISSEUR_SESSION = "prolians"

#: Entrées accumulées avant un appel d'enrichissement + une émission. Aligné sur
#: la taille de lot GraphQL : un appel batché par lot émis.
TAILLE_LOT = graphql_riche.TAILLE_LOT

#: Borne du chargement du sitemap (urllib en thread) : robots.txt → index →
#: sous-sitemaps est la phase la plus longue et la plus sujette au throttling.
TIMEOUT_SITEMAP_S = 300.0


@dataclass
class ParamsProliansApi:
    """Paramètres bornés de la méthode « api »."""

    #: Limite de produits émis (test / lot) ; ``None`` = tout le catalogue.
    limit: int | None = None
    #: Incrémental : ne garder que les entrées dont ``lastmod`` ≥ cette date ISO.
    depuis: str | None = None
    #: Enrichir prix/stock/fiche riche. ``False`` = énumération sitemap seule.
    enrichir: bool = True


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS PURS
# ═══════════════════════════════════════════════════════════════════════════════

def hors_perimetre(entree: dict, depuis: str | None) -> bool:
    """Vrai si l'entrée est antérieure au filtre incrémental ``depuis``. **Pur**."""
    lastmod = entree.get("lastmod")
    return bool(depuis and lastmod and lastmod < depuis)


def doit_vider(taille_lot: int, emis: int, limit: int | None) -> bool:
    """Vrai s'il faut vider le lot : lot plein **ou** limite atteinte pile. **Pur**.

    Le ``reste == 0`` garantit un ``limit`` **exact** (non arrondi à
    ``TAILLE_LOT``) → pas d'appel GraphQL de trop en mode test.
    """
    reste = limit - emis - taille_lot if limit else None
    return taille_lot >= TAILLE_LOT or reste == 0


def refs_du_lot(entrees: list[dict]) -> list[str]:
    """Références présentes dans le lot, dédupliquées, ordre conservé. **Pur**."""
    refs: list[str] = []
    vues: set[str] = set()
    for entree in entrees:
        ref = prolians_sitemap.ref_depuis_url(entree.get("url") or "")
        if ref and ref not in vues:
            vues.add(ref)
            refs.append(ref)
    return refs


def ligne_produit(entree: dict, enrichi: dict | None = None) -> dict:
    """Ligne produit = base sitemap + enrichissement GraphQL. **Pur**.

    ``entree`` : ``{url, lastmod, name, images}``. ``enrichi`` : dict de colonnes
    ``product_*`` dont les valeurs non vides complètent la base — les prix
    passent en dernier et font foi sur les champs communs.
    """
    url = entree.get("url") or ""
    base = element_produit(
        {
            "url": url,
            "ref": prolians_sitemap.ref_depuis_url(url),
            "designation": entree.get("name") or "",
            "images": entree.get("images") or [],
        },
        FOURNISSEUR,
    )
    if enrichi:
        base.update({cle: val for cle, val in enrichi.items() if val not in (None, "")})
    return base


def lignes_du_lot(entrees: list[dict], enrichissements: dict[str, dict]) -> list[dict]:
    """Lignes produit d'un lot. **Pur**.

    Une entrée sans référence exploitable, ou absente de l'enrichissement, garde
    sa base sitemap seule — la ligne reste valide.
    """
    sorties: list[dict] = []
    for entree in entrees:
        ref = prolians_sitemap.ref_depuis_url(entree.get("url") or "")
        produit = enrichissements.get(ref) if ref else None
        enrichi: dict | None = None
        if produit:
            enrichi = graphql_riche.champs_fiche_riche(produit)
            enrichi.update(graphql_riche.champs_enrichissement(produit))
        sorties.append(ligne_produit(entree, enrichi))
    return sorties


# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════

class ProliansApi(PlaywrightScraper):
    """Énumère le catalogue Prolians (sitemap) et émet des fiches enrichies."""

    FOURNISSEUR = FOURNISSEUR
    FOURNISSEUR_SESSION = FOURNISSEUR_SESSION
    CIBLE = "produits"

    def __init__(self, parametres: ParamsProliansApi | None = None, **kw) -> None:
        super().__init__(parametres or ParamsProliansApi(), **kw)

    async def run(self) -> dict:
        """Énumère le catalogue et émet par lots enrichis."""
        params: ParamsProliansApi = self.parametres
        emis = 0
        lot: list[dict] = []
        # Nommer l'étape rend le battement utile : « muet pendant l'amorçage » et
        # « muet pendant l'énumération » ne se diagnostiquent pas pareil.
        etape = "amorçage"
        try:
            async with self.battement(
                lambda: {"message": f"{etape} — {message_progression(self.pages_vues, emis)}"}
            ):
                enrichisseur = await self._preparer_enrichissement(params)

                etape = "énumération du sitemap"
                entrees = await asyncio.wait_for(
                    asyncio.to_thread(self._charger_entrees, params.depuis),
                    timeout=TIMEOUT_SITEMAP_S,
                )
                _log.info("Sitemap Prolians chargé : %d entrées à traiter.", len(entrees))

                etape = "extraction"
                for entree in entrees:
                    self.verifier_annulation()
                    lot.append(entree)
                    if doit_vider(len(lot), emis, params.limit):
                        emis += await self._emettre_lot(lot, enrichisseur)
                        derniere = lot[-1].get("url") or ""
                        lot = []
                        self.publier_reprise(
                            {"url": derniere},
                            {"message": message_progression(self.pages_vues, emis)},
                        )
                        if params.limit and emis >= params.limit:
                            return self._resultat(emis)
                    await asyncio.sleep(0)  # cède la main (annulation réactive)
                if lot:
                    emis += await self._emettre_lot(lot, enrichisseur)
        except ScrapeAnnule:
            _log.info("Scrape Prolians interrompu à la demande — %d fiche(s) écrites.", emis)
        finally:
            await self.persister_session()
            await self.fermer()

        self.progres({"message": message_progression(self.pages_vues, emis)})
        return self._resultat(emis)

    # ─── Étapes ──────────────────────────────────────────────────────────────

    def _charger_entrees(self, depuis: str | None) -> list[dict]:
        """Matérialise les entrées du sitemap après filtre incrémental. **Bloquant**.

        Exécuté en thread : ``iter_product_entries`` fait des fetchs urllib
        synchrones qu'on ne veut pas dans la boucle async, sous peine de geler le
        battement.
        """
        return [
            e for e in prolians_sitemap.iter_product_entries(logger=_log)
            if not hors_perimetre(e, depuis)
        ]

    async def _emettre_lot(self, lot: list[dict],
                           enrichisseur: graphql_riche.EnrichisseurGraphQL | None) -> int:
        """Enrichit le lot puis émet ses lignes. Retourne le nombre émis."""
        enrichissements: dict[str, dict] = {}
        if enrichisseur is not None and enrichisseur.pret and self._contexte is not None:
            enrichissements = await enrichisseur.recuperer(self._contexte, refs_du_lot(lot))
        lignes = lignes_du_lot(lot, enrichissements)
        for ligne in lignes:
            self.emettre_donnee(self.CIBLE, ligne)
        self.page_vue(len(lot))
        return len(lignes)

    async def _preparer_enrichissement(
        self, params: ParamsProliansApi
    ) -> graphql_riche.EnrichisseurGraphQL | None:
        """Ouvre le navigateur et amorce GraphQL. Lève si l'amorçage reste impossible.

        ⚠️ **Pas de dégradation silencieuse.** Retomber sur « énumération seule »
        quand l'amorçage échoue fait émettre des dizaines de milliers de produits
        **sans prix ni stock**, et le run finit « terminé » avec un gros compteur
        — un succès apparent. Mesuré une fois : 381 lignes avec prix sur 79 947.
        Cause : une session vieille de 16 jours dont la méta disait encore « ok ».

        On tente donc un auto-login puis on rejoue l'amorçage ; s'il échoue
        encore, on lève. ``enrichir=False`` reste un choix explicite et légitime.
        """
        if not params.enrichir:
            _log.info("Enrichissement désactivé — énumération sitemap seule.")
            return None

        session = GestionnaireSessions().chemin_session(FOURNISSEUR_SESSION)
        await self.demarrer_navigateur(storage_state=session if session.exists() else None)
        enrichisseur = graphql_riche.EnrichisseurGraphQL()

        if await self._amorcer(enrichisseur):
            return enrichisseur

        _log.warning("Amorçage GraphQL Prolians échoué — tentative d'auto-login.")
        if await self._relogin(session) and await self._amorcer(enrichisseur):
            _log.info("Amorçage GraphQL Prolians rétabli après re-login.")
            return enrichisseur

        raise RuntimeError(
            "Enrichissement GraphQL Prolians indisponible (session expirée ou "
            f"opération {graphql_riche.OP_PRIX_STOCK} non capturée). Run interrompu : "
            "sans lui le catalogue partirait sans prix ni stock. Relancer après un "
            "login manuel, ou demander explicitement enrichir=False."
        )

    async def _amorcer(self, enrichisseur: graphql_riche.EnrichisseurGraphQL) -> bool:
        """Une tentative d'amorçage sur une page neuve (best-effort)."""
        try:
            return await enrichisseur.amorcer(await self.nouvelle_page())
        except Exception as exc:  # une page qui casse ne doit pas masquer la cause
            _log.warning("Amorçage GraphQL Prolians en erreur : %s", exc)
            return False

    async def _relogin(self, session: Path) -> bool:
        """Rejoue l'auto-login puis recharge les cookies dans le contexte **ouvert**.

        On ne recrée pas le contexte : des requêtes peuvent être en vol et
        ``contexte.request`` partage le même pot de cookies — les remplacer suffit.
        """
        try:
            if not await login_auto(FOURNISSEUR_SESSION, session):
                return False
            # Horodater : sans ça un re-login réussi reste invisible et la session
            # fraîche est réputée expirée dès le prochain contrôle d'âge.
            GestionnaireSessions().marquer_connecte(FOURNISSEUR_SESSION)
            etat = json.loads(Path(session).read_text(encoding="utf-8"))
        except Exception as exc:
            _log.warning("Auto-login Prolians en échec : %s", exc)
            return False
        cookies = etat.get("cookies") or []
        if not cookies:
            return False
        await self._contexte.clear_cookies()
        await self._contexte.add_cookies(cookies)
        return True

    @staticmethod
    def _resultat(emis: int) -> dict:
        return {"produits": emis}
