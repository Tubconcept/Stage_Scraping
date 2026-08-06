"""
Méthode « sitemap » — catalogue Setin complet en HTTP concurrent (P5).

Remplace le crawl par catégories (``scrap_setin_products.py`` : menu 3 niveaux au
clic, jusqu'à 60 « voir plus » par catégorie, **une page navigateur par fiche**) :

1. **Énumération** — le sitemap liste ~20 000 fiches en HTTP simple, sans login
   (``setin_sitemap.iter_entrees_produit``), chacune avec un ``lastmod``
   → filtre incrémental.
2. **Fetch concurrent poli** — chaque fiche est récupérée en **HTTP** via
   l'``APIRequestContext`` Playwright (session rejouée → **prix compte**),
   **sans ouvrir de page** : jitter + back-off (``core.http_poli``).
3. **Parse statique** — ``setin_fiche_json.parser_fiche`` lit les variables JS
   inline : une variante = un article, prix **numérique**, quantité de stock,
   EAN, réf fabricant.
4. **Complétion des tarifs** — au-delà de 10 variantes, les tarifs manquants sont
   récupérés par ``setin_tarifs.completer``.

⚠️ **Encodage** : Setin sert de l'``iso-8859-1``. On lit donc les **octets**
(``get_poli_octets``) puis on décode via ``setin_fiche_json.decoder`` —
``reponse.text()`` de Playwright lèverait ``UnicodeDecodeError`` dès le premier
accent.

⚠️ **Session perdue** : sans session, ``json_tarifs`` porte les prix publics. La
fiche est **sautée** plutôt que publiée avec un mauvais prix — on ne remplace
jamais un prix compte par un prix catalogue. Les sessions B2B Setin sont courtes,
d'où le re-login à chaud de ``GardeSession`` (Setin n'a pas de captcha).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from core.f2 import element_produit
from core.garde_session import GardeSession, message_progression
from core.http_poli import entetes_navigateur, get_poli_octets
from core.reprise import enumerer_apres
from core.scrap_base import PlaywrightScraper, ScrapeAnnule
from core.sessions import GestionnaireSessions

from . import setin_fiche_json, setin_sitemap, setin_tarifs

_log = logging.getLogger(__name__)

FOURNISSEUR = "P5"
FOURNISSEUR_SESSION = "setin"

#: Entrées sitemap accumulées avant un round de fetch concurrent + émission.
TAILLE_LOT = 200

#: Borne du chargement du sitemap : ``urllib`` peut staller sous throttling.
TIMEOUT_SITEMAP_S = 300.0


@dataclass
class ParamsSetinSitemap:
    """Paramètres bornés de la méthode « sitemap »."""

    #: Limite de **fiches** traitées (test / lot) ; ``None`` = tout le catalogue.
    limit: int | None = None
    #: Requêtes HTTP simultanées. Borné : rester dans un trafic plausible.
    concurrence: int = 3
    #: Incrémental : ne garder que les fiches dont ``lastmod`` ≥ cette date ISO.
    depuis: str | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS PURS
# ═══════════════════════════════════════════════════════════════════════════════

def taille_a_traiter(taille_lot: int, faites: int, limit: int | None) -> int:
    """Combien d'entrées du lot traiter sans dépasser ``limit`` (en fiches). **Pur**."""
    if limit is None:
        return taille_lot
    return max(0, min(taille_lot, limit - faites))


def hors_perimetre(entree: dict, depuis: str | None) -> bool:
    """Vrai si l'entrée est antérieure au filtre incrémental ``depuis``. **Pur**."""
    lastmod = entree.get("lastmod")
    return bool(depuis and lastmod and lastmod < depuis)


# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════

class SetinSitemap(GardeSession, PlaywrightScraper):
    """Énumère le catalogue Setin (sitemap) et pousse des fiches (HTTP concurrent)."""

    FOURNISSEUR = FOURNISSEUR
    FOURNISSEUR_SESSION = FOURNISSEUR_SESSION
    CIBLE = "produits"

    def __init__(self, parametres: ParamsSetinSitemap | None = None, **kw) -> None:
        super().__init__(parametres or ParamsSetinSitemap(), **kw)
        self._init_garde_session()

    async def run(self) -> dict:
        params: ParamsSetinSitemap = self.parametres
        session = GestionnaireSessions().chemin_session(FOURNISSEUR_SESSION)
        if not session.exists():
            _log.warning(
                "Session Setin absente — les fiches seront sautées (pas de prix compte)."
            )

        emis = 0            # lignes écrites (1 par variante)
        fiches = 0          # fiches traitées → pilote ``limit``
        deconnectees = 0    # fiches sautées car servies déconnectées
        lot: list[dict] = []
        depuis_reprise = self.reprise.get("derniere_url")
        if depuis_reprise:
            _log.info("Reprise après %s : les fiches précédentes sont sautées.", depuis_reprise)

        try:
            async with self.battement(
                lambda: {
                    "message": message_progression(self.pages_vues, emis, deconnectees),
                    "pages": self.pages_vues,
                    "produits": emis,
                }
            ):
                # Démarrage du navigateur **sous battement** : lancer Chromium et
                # charger le storage_state peut staller (verrou de profil, antibot).
                await self.demarrer_navigateur(
                    storage_state=session if session.exists() else None
                )
                entetes = entetes_navigateur()
                entrees = await asyncio.wait_for(
                    asyncio.to_thread(self._charger_entrees, depuis_reprise, params.depuis),
                    timeout=TIMEOUT_SITEMAP_S,
                )
                _log.info("Sitemap Setin chargé : %d fiches à traiter.", len(entrees))

                for entree in entrees:
                    self.verifier_annulation()
                    lot.append(entree)
                    if len(lot) >= TAILLE_LOT:
                        n_fiches, n_emis, n_deco = await self._traiter_lot(lot, entetes, fiches)
                        fiches += n_fiches
                        emis += n_emis
                        deconnectees += n_deco
                        if n_fiches:
                            self.publier_reprise(
                                {"derniere_url": lot[n_fiches - 1]["url"], "fiches": fiches},
                                {"message": message_progression(fiches, emis, deconnectees)},
                            )
                        lot = []
                        if params.limit and fiches >= params.limit:
                            return self._resultat(emis, deconnectees)
                    await asyncio.sleep(0)  # cède la main (annulation réactive)
                if lot:
                    _, n_emis, n_deco = await self._traiter_lot(lot, entetes, fiches)
                    emis += n_emis
                    deconnectees += n_deco
        except ScrapeAnnule:
            _log.info("Scrape Setin interrompu à la demande — %d ligne(s) écrites.", emis)
        finally:
            # Ne réécrire la session que si le run a produit quelque chose : sinon on
            # risque de figer sur disque les cookies d'une session déjà morte.
            if emis > 0:
                await self.persister_session()
            await self.fermer()

        if deconnectees:
            _log.warning("%d fiche(s) sautée(s) — page déconnectée (session perdue).",
                         deconnectees)
        self.progres({"message": message_progression(fiches, emis, deconnectees)})
        return self._resultat(emis, deconnectees)

    # ─── Étapes ──────────────────────────────────────────────────────────────

    def _charger_entrees(self, depuis_reprise: str | None,
                         depuis_incr: str | None) -> list[dict]:
        """Fiches du sitemap après reprise + filtre incrémental. **Bloquant**.

        Exécuté en thread : ``iter_entrees_produit`` fait des fetchs urllib
        synchrones qu'on ne veut pas dans la boucle async (gel du battement).
        """
        apres = enumerer_apres(
            setin_sitemap.iter_entrees_produit, depuis_reprise, lambda e: e["url"]
        )
        return [e for e in apres if not hors_perimetre(e, depuis_incr)]

    async def _traiter_lot(self, lot: list[dict], entetes: dict[str, str],
                           fiches: int) -> tuple[int, int, int]:
        """Fetch concurrent + parse + émission. Retourne ``(fiches, émis, déconnectées)``."""
        a_traiter = lot[:taille_a_traiter(len(lot), fiches, self.parametres.limit)]
        if not a_traiter:
            return (0, 0, 0)
        semaphore = asyncio.Semaphore(self.parametres.concurrence)
        listes = await asyncio.gather(
            *(self._traiter_fiche(entree, entetes, semaphore) for entree in a_traiter)
        )
        emis = deco = 0
        for lignes, est_deco in listes:
            if est_deco:
                deco += 1
            for ligne in lignes:
                self.emettre_donnee(self.CIBLE, ligne)
                emis += 1
        return (len(a_traiter), emis, deco)

    async def _traiter_fiche(self, entree: dict, entetes: dict[str, str],
                             semaphore: asyncio.Semaphore) -> tuple[list[dict], bool]:
        """Une fiche → ``(lignes produit, déconnectée ?)``.

        Page déconnectée → **re-login à chaud** puis une seule reprise ; si la
        session ne revient pas, la fiche est sautée. On ne persiste jamais une
        fiche déconnectée : Setin sert alors les prix publics.
        """
        url = entree["url"]
        octets = await self._recuperer(url, entetes, semaphore)
        if not octets:
            _log.debug("Fiche non récupérée : %s", url)
            return ([], False)

        html = setin_fiche_json.decoder(octets)
        if setin_fiche_json.page_deconnectee(html):
            generation = self.generation_session
            if not await self.rafraichir_session(generation):
                return ([], self.noter_deconnexion())
            octets = await self._recuperer(url, entetes, semaphore)
            html = setin_fiche_json.decoder(octets) if octets else ""
            if not html or setin_fiche_json.page_deconnectee(html):
                return ([], self.noter_deconnexion())

        self.succes_fiche()
        fiche = setin_fiche_json.parser_fiche(html, url)
        if not fiche.variantes:
            return ([], False)
        if fiche.a_synchroniser:
            # Au-delà de 10 variantes, les tarifs restants ne sont PAS dans la page.
            async with semaphore:
                complements = await setin_tarifs.completer(
                    self._contexte.request, fiche.a_synchroniser, referer=url
                )
            self._appliquer_tarifs(fiche.variantes, complements)
        return ([element_produit(v.extrait, FOURNISSEUR) for v in fiche.variantes], False)

    async def _recuperer(self, url: str, entetes: dict[str, str],
                         semaphore: asyncio.Semaphore) -> bytes | None:
        """Un GET poli, compté comme une fiche parcourue.

        ``get_poli_octets`` et non ``get_poli`` : Setin sert de l'iso-8859-1 et
        ``reponse.text()`` lèverait ``UnicodeDecodeError``.
        """
        async with semaphore:
            octets = await get_poli_octets(self._contexte.request, url, entetes=entetes)
        self.page_vue()
        return octets

    @staticmethod
    def _appliquer_tarifs(variantes: list[setin_fiche_json.Variante],
                          complements: dict) -> None:
        """Injecte les tarifs récupérés après coup dans les extraits (en place)."""
        for variante in variantes:
            tarif = complements.get(variante.id_var)
            if tarif:
                variante.extrait.update(setin_fiche_json.champs_tarif(tarif))

    @staticmethod
    def _resultat(emis: int, deconnectees: int) -> dict:
        resultat = {"produits": emis}
        if deconnectees:
            resultat["deconnectees"] = deconnectees
        return resultat
