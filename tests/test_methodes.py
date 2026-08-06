"""
Tests des méthodes de scraping portées de SCRAPPER_App.

Couvre les parties **pures** — celles où une régression est silencieuse :

  core/f2           — mapping extrait → colonnes CSV_HEADERS, sans déclinaisons
  core/reprise      — saut d'énumération et repli
  core/http_poli    — back-off
  core/sessions     — présence, âge, expiration
  core/sink         — comptage et tolérance aux erreurs unitaires
  core/methodes     — résolution des méthodes et validation des paramètres
  Prolians          — mapping GraphQL (fiche riche + prix), découpe des lots
  Setin             — sitemap, variables JS inline, tarifs, montants
  Legallais         — sitemap, fiche HTML, codes article, mapping prix

Le **live** (réseau, navigateur) n'est pas couvert : il se valide en lançant
l'app.

Lancement : pytest tests/test_methodes.py -v
"""

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.config import CSV_HEADERS  # noqa: E402
from core.f2 import COLONNES_DECLINAISON, colonnes_inconnues, element_produit  # noqa: E402
from core.http_poli import delai_avant_reessai, entetes_navigateur  # noqa: E402
from core.reprise import enumerer_apres, reprendre_categories  # noqa: E402
from core.sink import SinkMemoire  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# core/f2
# ═══════════════════════════════════════════════════════════════════════════════

class TestElementProduit:

    def test_base_minimale(self):
        ligne = element_produit({"url": "https://a.fr/p", "ref": "R1"}, "P3")
        assert ligne["product_fournisseur"] == "P3"
        assert ligne["product_fournisseur_url"] == "https://a.fr/p"
        assert ligne["product_reference_fournisseur"] == "R1"

    def test_champ_vide_omis(self):
        """Un champ absent ne doit pas écraser une valeur déjà en base."""
        ligne = element_produit({"url": "https://a.fr/p", "prix": "", "ean": None}, "P3")
        assert "product_price_ht" not in ligne
        assert "product_ean" not in ligne

    def test_listes_jointes(self):
        ligne = element_produit(
            {"url": "u", "categories": ["Outillage", "Perçage"],
             "images": ["a.jpg", "b.jpg"], "docs": ["d.pdf"]},
            "P5",
        )
        assert ligne["product_category_tree"] == "Outillage > Perçage"
        assert ligne["product_image_url"] == "a.jpg||b.jpg"
        assert ligne["product_docs_url"] == "d.pdf"

    def test_listes_vides_omises(self):
        ligne = element_produit({"url": "u", "categories": [], "images": [None, ""]}, "P5")
        assert "product_category_tree" not in ligne
        assert "product_image_url" not in ligne

    def test_attributs_en_json(self):
        ligne = element_produit({"url": "u", "attributs": {"Finition": "Inox", "Vide": ""}}, "P5")
        assert json.loads(ligne["product_attributes"]) == {"Finition": "Inox"}

    def test_aucune_colonne_de_declinaison(self):
        """Décision : tout est traité comme un article simple."""
        ligne = element_produit(
            {"url": "u", "ref": "R", "designation": "D", "prix": "1", "ean": "3",
             "attributs": {"a": "b"}, "categories": ["c"], "images": ["i"]},
            "P1",
        )
        assert not COLONNES_DECLINAISON & set(ligne)

    def test_uniquement_des_colonnes_connues(self):
        """Une clé hors CSV_HEADERS serait ignorée en silence par save_product."""
        ligne = element_produit(
            {"url": "u", "ref": "R", "designation": "D", "prix": "1", "eco": "0.5",
             "stock": "EN STOCK", "marque": "M", "marque_logo": "l.png", "ean": "3",
             "ref_fabricant": "RF", "description": "desc", "conditionnement": "10",
             "promotion": "9", "eco_label": "A", "statut": "actif",
             "categories": ["c"], "images": ["i"], "docs": ["d"], "cross_sell": ["x"],
             "attributs": {"a": "b"}},
            "P1",
        )
        assert colonnes_inconnues(ligne) == set()
        assert set(ligne) <= set(CSV_HEADERS)


# ═══════════════════════════════════════════════════════════════════════════════
# core/reprise
# ═══════════════════════════════════════════════════════════════════════════════

class TestReprise:

    @staticmethod
    def _fabrique():
        return [{"url": "a"}, {"url": "b"}, {"url": "c"}]

    def test_sans_point_de_reprise(self):
        assert len(list(enumerer_apres(self._fabrique, None, lambda e: e["url"]))) == 3

    def test_saute_jusqu_au_point(self):
        restants = list(enumerer_apres(self._fabrique, "a", lambda e: e["url"]))
        assert [e["url"] for e in restants] == ["b", "c"]

    def test_point_de_reprise_lui_meme_non_rejoue(self):
        restants = list(enumerer_apres(self._fabrique, "c", lambda e: e["url"]))
        assert restants == []

    def test_repli_si_point_disparu(self):
        """Le catalogue a bougé : mieux vaut tout refaire que de tout sauter."""
        restants = list(enumerer_apres(self._fabrique, "zzz", lambda e: e["url"]))
        assert len(restants) == 3

    def test_categories_sans_checkpoint(self):
        assert reprendre_categories(["a", "b"], None) == (["a", "b"], 1)

    def test_categories_reprend_page_suivante(self):
        restants, page = reprendre_categories(["a", "b", "c"], {"categorie": "b", "page": 3})
        assert restants == ["b", "c"]
        assert page == 4

    def test_categories_sans_page_refait_la_categorie(self):
        restants, page = reprendre_categories(["a", "b"], {"categorie": "b"})
        assert restants == ["b"]
        assert page == 1

    def test_categorie_disparue_repart_du_debut(self):
        assert reprendre_categories(["a", "b"], {"categorie": "z", "page": 2}) == (["a", "b"], 1)


# ═══════════════════════════════════════════════════════════════════════════════
# core/http_poli
# ═══════════════════════════════════════════════════════════════════════════════

class TestHttpPoli:

    def test_pas_de_reessai_sur_404(self):
        assert delai_avant_reessai(404, 0) is None

    def test_backoff_exponentiel_borne(self):
        assert delai_avant_reessai(429, 0) == 1.5
        assert delai_avant_reessai(429, 1) == 3.0
        assert delai_avant_reessai(429, 50) == 30.0

    def test_403_est_reessaye(self):
        """403 = blocage antibot transitoire, pas une absence définitive."""
        assert delai_avant_reessai(403, 0) is not None

    def test_ua_de_session_prioritaire(self):
        entetes = entetes_navigateur("MonUA/1.0")
        assert entetes["User-Agent"] == "MonUA/1.0"


# ═══════════════════════════════════════════════════════════════════════════════
# core/sessions
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessions:

    @pytest.fixture
    def gestionnaire(self, tmp_path):
        from core.sessions import GestionnaireSessions
        return GestionnaireSessions(tmp_path)

    def test_absente(self, gestionnaire):
        from core.sessions import STATUT_ABSENTE
        assert gestionnaire.etat("setin") == STATUT_ABSENTE

    def test_ok_apres_marquage(self, gestionnaire):
        from core.sessions import STATUT_OK
        chemin = gestionnaire.chemin_session("setin")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text("{}", encoding="utf-8")
        gestionnaire.marquer_connecte("setin")
        assert gestionnaire.etat("setin") == STATUT_OK

    def test_expiree_par_age(self, gestionnaire):
        """Une session vieille est morte même si sa méta dit « ok »."""
        from core.sessions import STATUT_EXPIREE
        chemin = gestionnaire.chemin_session("setin")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text("{}", encoding="utf-8")
        (chemin.parent / "meta.json").write_text(
            json.dumps({"statut": "ok", "derniere_connexion": "2020-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        assert gestionnaire.etat("setin") == STATUT_EXPIREE

    def test_meta_illisible_ne_presume_pas_l_expiration(self, gestionnaire):
        from core.sessions import STATUT_OK
        chemin = gestionnaire.chemin_session("setin")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text("{}", encoding="utf-8")
        (chemin.parent / "meta.json").write_text("pas du json", encoding="utf-8")
        assert gestionnaire.etat("setin") == STATUT_OK
        assert gestionnaire.age_jours("setin") is None


# ═══════════════════════════════════════════════════════════════════════════════
# core/sink
# ═══════════════════════════════════════════════════════════════════════════════

class TestSink:

    def test_sink_memoire(self):
        sink = SinkMemoire()
        sink.ajouter("produits", {"a": 1})
        sink.ajouter("produits", {"a": 2})
        assert sink.bilan() == {"produits": 2}

    def test_erreur_unitaire_ne_tue_pas_le_run(self, monkeypatch):
        """Perdre une fiche est regrettable ; perdre des heures de scrape ne l'est pas."""
        import core.sink as mod

        monkeypatch.setattr(mod, "save_product", lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("colonne trop courte")))
        sink = mod.SinkMariaDB("setin")
        sink.ajouter("produits", {"product_fournisseur_url": "u"})
        assert sink.bilan()["erreurs"] == 1

    def test_compte_les_resultats_d_ecriture(self, monkeypatch):
        import core.sink as mod

        reponses = iter(["insert", "update", "inchange"])
        monkeypatch.setattr(mod, "save_product", lambda *_a, **_k: next(reponses))
        sink = mod.SinkMariaDB("setin")
        for _ in range(3):
            sink.ajouter("produits", {})
        bilan = sink.bilan()
        assert (bilan["nouveaux"], bilan["enrichis"], bilan["inchanges"]) == (1, 1, 1)

    def test_cible_inconnue_comptee_en_erreur(self, monkeypatch):
        import core.sink as mod

        monkeypatch.setattr(mod, "save_product", lambda *_a, **_k: "insert")
        sink = mod.SinkMariaDB("setin")
        sink.ajouter("licornes", {})
        assert sink.bilan()["erreurs"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# core/scrap_base — le contrat
# ═══════════════════════════════════════════════════════════════════════════════

class TestContratScraper:

    @staticmethod
    def _scraper(**kw):
        from core.scrap_base import Scraper

        class Bidon(Scraper):
            async def run(self):
                return {"produits": 0}

        return Bidon(None, **kw)

    def test_sans_dependances_injectees(self):
        """Un scraper doit s'instancier nu (tests, script) sans rien casser."""
        scraper = self._scraper()
        scraper.progres({"message": "x"})       # no-op
        scraper.emettre_donnee("produits", {})  # no-op
        assert scraper.should_stop() is False

    def test_page_vue_compteur_vivant(self):
        scraper = self._scraper()
        scraper.page_vue()
        scraper.page_vue(4)
        assert scraper.pages_vues == 5

    def test_emission_vers_le_sink(self):
        sink = SinkMemoire()
        scraper = self._scraper(sink=sink)
        scraper.emettre_donnee("produits", {"a": 1})
        assert sink.elements["produits"] == [{"a": 1}]

    def test_publier_reprise_joint_la_progression(self):
        from core.scrap_base import CLE_REPRISE

        charges = []
        scraper = self._scraper(emettre_progres=charges.append)
        scraper.publier_reprise({"url": "u"}, {"message": "m"})
        assert charges[0][CLE_REPRISE] == {"url": "u"}
        assert charges[0]["message"] == "m"

    def test_annulation_cooperative(self):
        from core.scrap_base import ScrapeAnnule

        scraper = self._scraper(doit_annuler=lambda: True)
        assert scraper.should_stop() is True
        with pytest.raises(ScrapeAnnule):
            scraper.verifier_annulation()

    def test_battement_publie_puis_s_arrete(self):
        """Le battement doit cesser à la sortie du bloc, sans tâche orpheline."""
        import asyncio

        charges = []
        scraper = self._scraper(emettre_progres=charges.append)

        async def _scenario():
            async with scraper.battement(lambda: {"message": "vivant"}, intervalle=0.01):
                await asyncio.sleep(0.05)
            avant = len(charges)
            await asyncio.sleep(0.05)
            return avant, len(charges)

        avant, apres = asyncio.run(_scenario())
        assert avant >= 1        # a bien battu pendant le bloc
        assert apres == avant    # et plus rien après


# ═══════════════════════════════════════════════════════════════════════════════
# core/methodes
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegistre:

    def test_defaut_en_tete(self):
        from core.methodes import methodes_disponibles, voie
        noms = methodes_disponibles("produits", "setin")
        assert noms[0] == voie("produits", "setin").defaut

    def test_resolution_par_defaut(self):
        from core.methodes import resoudre
        methode, params = resoudre("produits", "prolians")
        assert methode.fabrique.__name__ == "ProliansApi"
        assert params.enrichir is True

    def test_couple_inconnu(self):
        from core.methodes import MethodeInconnue, resoudre
        with pytest.raises(MethodeInconnue):
            resoudre("produits", "sonepar")

    def test_methode_inconnue(self):
        from core.methodes import MethodeInconnue, resoudre
        with pytest.raises(MethodeInconnue):
            resoudre("produits", "setin", "telepathie")

    def test_parametre_inconnu_refuse(self):
        """Un paramètre mal orthographié doit se voir, pas se perdre en silence."""
        from core.methodes import ParametresInvalides, resoudre
        with pytest.raises(ParametresInvalides):
            resoudre("produits", "setin", "sitemap", {"limite": 10})

    def test_parametres_appliques(self):
        from core.methodes import resoudre
        _, params = resoudre("produits", "setin", "sitemap", {"limit": 5, "concurrence": 2})
        assert (params.limit, params.concurrence) == (5, 2)

    def test_libelle_lisible(self):
        from core.methodes import libelle
        assert "GraphQL" in libelle("produits", "prolians", "api")


# ═══════════════════════════════════════════════════════════════════════════════
# Prolians — méthode « api »
# ═══════════════════════════════════════════════════════════════════════════════

class TestProlians:

    def test_ref_depuis_url_numerique_et_encodee(self):
        """Ne PAS restreindre au numérique : les réfs encodées sortiraient sans prix."""
        from scrapers.Prolians_P3.products.prolians_sitemap import ref_depuis_url
        assert ref_depuis_url("https://www.prolians.fr/trepan-10013933") == "10013933"
        assert ref_depuis_url("https://www.prolians.fr/trepan-03hswte") == "03HSWTE"
        assert ref_depuis_url("") == ""

    def test_refs_du_lot_dedupliquees(self):
        from scrapers.Prolians_P3.products.methode_api import refs_du_lot
        lot = [{"url": "a-111111"}, {"url": "b-111111"}, {"url": "c-222222"}]
        assert refs_du_lot(lot) == ["111111", "222222"]

    def test_doit_vider_lot_plein(self):
        from scrapers.Prolians_P3.products.methode_api import TAILLE_LOT, doit_vider
        assert doit_vider(TAILLE_LOT, 0, None) is True
        assert doit_vider(TAILLE_LOT - 1, 0, None) is False

    def test_doit_vider_limite_exacte(self):
        """Sans ça, ``limit`` serait arrondi à la taille de lot."""
        from scrapers.Prolians_P3.products.methode_api import doit_vider
        assert doit_vider(5, 0, 5) is True

    def test_hors_perimetre_incremental(self):
        from scrapers.Prolians_P3.products.methode_api import hors_perimetre
        assert hors_perimetre({"lastmod": "2026-01-01"}, "2026-06-01") is True
        assert hors_perimetre({"lastmod": "2026-07-01"}, "2026-06-01") is False
        assert hors_perimetre({}, "2026-06-01") is False

    def test_statut_stock(self):
        from scrapers.Prolians_P3.products.graphql_riche import statut_stock
        assert statut_stock({"availability": [{"a": 1}]}) == "disponible"
        assert statut_stock({"availability": []}) == "non disponible"
        assert statut_stock({"isSellable": False, "availability": [{"a": 1}]}) == "non disponible"

    def test_fil_ariane_retire_racine_et_produit(self):
        from scrapers.Prolians_P3.products.graphql_riche import fil_ariane
        produit = {"breadcrumbs": [{"name": "Produits"}, {"name": "Outillage"},
                                   {"name": "Perçage"}, {"name": "Trépan 40"}]}
        assert fil_ariane(produit) == "Outillage > Perçage"

    def test_images_preferent_desktop(self):
        from scrapers.Prolians_P3.products.graphql_riche import images_produit
        produit = {"images": [{"set": [{"url": "m.jpg", "media": "MOBILE"},
                                       {"url": "d.jpg", "media": "DESKTOP"}]}]}
        assert images_produit(produit) == ["d.jpg"]

    def test_images_repli_si_variante_absente(self):
        from scrapers.Prolians_P3.products.graphql_riche import images_produit
        assert images_produit({"images": [{"set": [{"url": "x.jpg", "media": "TABLET"}]}]}) == \
            ["x.jpg"]

    def test_attributs_completes_sans_ecraser(self):
        from scrapers.Prolians_P3.products.graphql_riche import attributs_produit
        produit = {
            "technicalSpecs": [{"label": "Diamètre", "value": "40"}],
            "mainTechnicalSpecs": [{"label": "Diamètre", "value": "IGNORÉ"},
                                   {"label": "Matière", "value": "Carbure"}],
        }
        assert attributs_produit(produit) == {"Diamètre": "40", "Matière": "Carbure"}

    def test_eco_participation_somme_les_filieres(self):
        from scrapers.Prolians_P3.products.graphql_riche import eco_participation
        assert eco_participation({"ecoPart": {"deee": 0.5, "pmcb": 0.25}}) == "0.75"

    def test_eco_participation_absente_rend_vide(self):
        """Ne pas affirmer « 0 » là où la donnée est simplement manquante."""
        from scrapers.Prolians_P3.products.graphql_riche import eco_participation
        assert eco_participation({"ecoPart": {}}) == ""
        assert eco_participation({}) == ""

    def test_fiche_riche_sans_url(self):
        """L'identité de la fiche est l'URL d'énumération, pas ``urlKey``."""
        from scrapers.Prolians_P3.products.graphql_riche import champs_fiche_riche
        champs = champs_fiche_riche({"urlKey": "autre-slug", "description": "d"})
        assert "product_fournisseur_url" not in champs
        assert champs["product_description"] == "d"

    def test_enrichissement_sans_declinaisons(self):
        from scrapers.Prolians_P3.products.graphql_riche import champs_enrichissement
        champs = champs_enrichissement({"parentReference": "PARENT", "name": "N",
                                        "price": {"exclTax": 12.5}})
        assert "product_parent_reference" not in champs
        assert champs["product_price_ht"] == "12.5"

    def test_lignes_du_lot_fusionne(self):
        from scrapers.Prolians_P3.products.methode_api import lignes_du_lot
        lot = [{"url": "https://www.prolians.fr/trepan-10013933", "name": "Trépan"}]
        enrichissements = {"10013933": {"name": "Trépan carbure", "description": "Longue",
                                        "price": {"exclTax": 89.88},
                                        "availability": [{"a": 1}]}}
        ligne = lignes_du_lot(lot, enrichissements)[0]
        assert ligne["product_designation"] == "Trépan carbure"  # l'API fait foi
        assert ligne["product_price_ht"] == "89.88"
        assert ligne["product_description"] == "Longue"
        assert colonnes_inconnues(ligne) == set()

    def test_lignes_du_lot_sans_enrichissement(self):
        """Une entrée non enrichie garde sa base sitemap : la ligne reste valide."""
        from scrapers.Prolians_P3.products.methode_api import lignes_du_lot
        ligne = lignes_du_lot([{"url": "https://www.prolians.fr/x-999999", "name": "X"}], {})[0]
        assert ligne["product_reference_fournisseur"] == "999999"
        assert "product_price_ht" not in ligne


# ═══════════════════════════════════════════════════════════════════════════════
# Setin — méthode « sitemap »
# ═══════════════════════════════════════════════════════════════════════════════

_HTML_SETIN = """
<div class="info-perso">compte</div>
<div class="fil_ariane_fond">
  <a class="ariane-thematique-link">Cuisine</a>
  <a class="ariane-thematique-link">Tiroirs</a>
  <a class="ariane-thematique-link">Le produit</a>
</div>
<div class="stock_variante" data-id_var='13'><span class="texte">En stock Groupe</span></div>
<div class="stock_variante_agences" data-id_var='13'><span class="texte">IGNORÉ</span></div>
<div class="ligne_tableau" data-id="13">
  <div class="photo_variante"><img src="https://x/i.jpg"></div>
</div>
<script>
var json_article = {"nom_marque": "BLUM",
  "description_longue": "<div class='carac'><b>Finition</b><span>Inox</span></div>"};
var json_data = {"0": {"ref": "GABARIT"},
  "13": {"ref": "BLU293", "designation": "ATTACHE", "code_barre": "9009494117592",
         "ref_fournisseur": "ZSF.39", "conditionnement": "10"}};
var json_stock = {"13": "10.00"};
var json_tarifs = {"13": {"basePrice": 0.7412, "haveDeal": false, "ecoTax": 0}};
var json_variantes_to_sync = [14, 15];
</script>
"""


class TestSetin:

    def test_sitemaps_produit_filtres(self):
        from scrapers.Setin_P5.products.setin_sitemap import sitemaps_produit
        locs = ["x/siteMapsFRProduit1.xml", "x/siteMapsFRImage1.xml",
                "x/siteMapsFRCategorie1.xml", "x/siteMapsFRProduit2.xml"]
        assert sitemaps_produit(locs) == ["x/siteMapsFRProduit1.xml", "x/siteMapsFRProduit2.xml"]

    def test_decodage_iso_8859_1(self):
        """``reponse.text()`` de Playwright lèverait UnicodeDecodeError ici."""
        from scrapers.Setin_P5.products.setin_fiche_json import decoder
        assert decoder("Réappro.".encode("iso-8859-1")) == "Réappro."

    def test_extraire_var_accolades_dans_chaine(self):
        """« Coulisse {40} » tronquerait le littéral avec un comptage naïf."""
        from scrapers.Setin_P5.products.setin_fiche_json import extraire_var
        html = 'var json_data = {"13": {"ref": "A{1}B"}}; var autre = 1;'
        assert extraire_var(html, "json_data") == {"13": {"ref": "A{1}B"}}

    def test_extraire_var_absente(self):
        from scrapers.Setin_P5.products.setin_fiche_json import extraire_var
        assert extraire_var("rien ici", "json_data") is None

    def test_page_deconnectee(self):
        from scrapers.Setin_P5.products.setin_fiche_json import page_deconnectee
        assert page_deconnectee("<div>rien</div>") is True
        assert page_deconnectee('<div class="info-perso">x</div>') is False

    def test_formater_montant(self):
        from scrapers.Setin_P5.products.setin_fiche_json import formater_montant
        assert formater_montant(29.719999999999999) == "29.72"
        assert formater_montant("12,50") == "12.5"
        assert formater_montant(0) == ""
        assert formater_montant(None) == ""

    def test_champs_tarif_sans_promo(self):
        """Sans ``haveDeal``, publier le prix barré ferait croire à une promo à 0 %."""
        from scrapers.Setin_P5.products.setin_fiche_json import champs_tarif
        champs = champs_tarif({"basePrice": 10, "basePriceHorsPromo": 10, "haveDeal": False})
        assert champs == {"prix": "10"}

    def test_champs_tarif_avec_promo(self):
        from scrapers.Setin_P5.products.setin_fiche_json import champs_tarif
        champs = champs_tarif({"basePrice": 8, "basePriceHorsPromo": 10, "haveDeal": True,
                               "ecoTax": 0.5})
        assert champs == {"prix": "8", "promotion": "10", "eco": "0.5"}

    def test_parser_fiche_saute_le_gabarit(self):
        from scrapers.Setin_P5.products.setin_fiche_json import parser_fiche
        fiche = parser_fiche(_HTML_SETIN, "https://www.setin.fr/kit.html")
        assert [v.id_var for v in fiche.variantes] == ["13"]

    def test_parser_fiche_url_par_variante(self):
        """L'``?idvar=`` distingue deux articles de la même page → deux identités."""
        from scrapers.Setin_P5.products.setin_fiche_json import parser_fiche
        fiche = parser_fiche(_HTML_SETIN, "https://www.setin.fr/kit.html")
        assert fiche.variantes[0].extrait["url"].endswith("?idvar=BLU293")

    def test_parser_fiche_contenu(self):
        from scrapers.Setin_P5.products.setin_fiche_json import parser_fiche
        extrait = parser_fiche(_HTML_SETIN, "https://www.setin.fr/kit.html").variantes[0].extrait
        assert extrait["ean"] == "9009494117592"
        assert extrait["marque"] == "BLUM"
        assert extrait["stock"] == "En stock Groupe"  # libellé, pas la quantité
        assert extrait["categories"] == ["Cuisine", "Tiroirs"]  # sans le produit
        assert extrait["images"] == ["https://x/i.jpg"]
        assert extrait["attributs"] == {"Finition": "Inox"}
        assert extrait["prix"] == "0.7412"

    def test_variantes_a_synchroniser(self):
        from scrapers.Setin_P5.products.setin_fiche_json import parser_fiche
        assert parser_fiche(_HTML_SETIN, "u").a_synchroniser == [14, 15]

    def test_ligne_produit_valide(self):
        from scrapers.Setin_P5.products.setin_fiche_json import parser_fiche
        extrait = parser_fiche(_HTML_SETIN, "https://www.setin.fr/kit.html").variantes[0].extrait
        ligne = element_produit(extrait, "P5")
        assert colonnes_inconnues(ligne) == set()
        assert not COLONNES_DECLINAISON & set(ligne)

    def test_corps_formulaire_jquery(self):
        """``ids=1,2`` ne serait pas vu comme un tableau par le PHP en face."""
        from scrapers.Setin_P5.products.setin_tarifs import corps_formulaire
        assert corps_formulaire([13, 14]) == "ids%5B%5D=13&ids%5B%5D=14&from=fiche_article"

    def test_paquets(self):
        from scrapers.Setin_P5.products.setin_tarifs import paquets
        assert paquets([1, 2, 3], 2) == [[1, 2], [3]]
        with pytest.raises(ValueError):
            paquets([1], 0)

    def test_tarifs_depuis_reponse(self):
        from scrapers.Setin_P5.products.setin_tarifs import tarifs_depuis_reponse
        assert tarifs_depuis_reponse('{"tarifs": {"14": {"basePrice": 1}}}') == \
            {"14": {"basePrice": 1}}
        assert tarifs_depuis_reponse("pas du json") == {}

    def test_taille_a_traiter(self):
        from scrapers.Setin_P5.products.methode_sitemap import taille_a_traiter
        assert taille_a_traiter(200, 0, None) == 200
        assert taille_a_traiter(200, 190, 195) == 5
        assert taille_a_traiter(200, 200, 195) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Legallais — méthode « sitemap »
# ═══════════════════════════════════════════════════════════════════════════════

_XML_INDEX = b"""<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.legallais.com/sitemap.products.1.xml</loc></sitemap>
  <sitemap><loc>https://www.legallais.com/sitemap.categories.xml</loc></sitemap>
</sitemapindex>"""

_XML_URLSET = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.legallais.com/produit/vis/12345</loc></url>
  <url><loc>https://www.legallais.com/produit/ecrou/67890</loc></url>
</urlset>"""

_HTML_LEGALLAIS = """
<meta property="og:title" content="Embout TX20">
<meta itemprop="description" content="Un embout de vissage">
<div itemprop="brand"><meta itemprop="name" content="MILWAUKEE"></div>
<a class="js-product__image-link" href="https://img/1.jpg"></a>
<a class="js-product__image-link" href="https://img/1.jpg"></a>
<a class="js-product__image-link" href="https://img/2.jpg"></a>
<span class="code_ean"><span class="code_ean_value">3660000000001</span></span>
<table id="characteristicsTable">
  <tr><th>Unité de vente</th><td>Boîte de 10</td></tr>
  <tr><th>Référence fabricant</th><td>4932430859</td></tr>
</table>
<a href="javascript:window.open('https://doc/fiche.pdf')">PDF</a>
<script type="application/ld+json">
{"@type": "BreadcrumbList", "itemListElement": [
  {"name": "Accueil"}, {"name": "Outillage"}, {"name": "Embouts"}]};
</script>
<tr class="c-references-articles__table__line" data-article-code="104802"></tr>
<tr class="c-references-articles__table__line" data-article-code="104803"></tr>
<tr class="c-references-articles__table__line" data-article-code="104802"></tr>
"""


class TestLegallais:

    def test_classer_index(self):
        from scrapers.Legallais_P1.products.legallais_sitemap import classer_document
        genre, locs = classer_document(_XML_INDEX)
        assert genre == "index"
        assert len(locs) == 2

    def test_classer_urlset(self):
        from scrapers.Legallais_P1.products.legallais_sitemap import classer_document
        assert classer_document(_XML_URLSET) == ("urlset", [])

    def test_iter_locs(self):
        from scrapers.Legallais_P1.products.legallais_sitemap import iter_locs
        assert len(list(iter_locs(_XML_URLSET))) == 2

    def test_ref_depuis_url(self):
        from scrapers.Legallais_P1.products.legallais_sitemap import ref_depuis_url
        assert ref_depuis_url("https://www.legallais.com/produit/vis/12345") == "12345"
        assert ref_depuis_url("https://www.legallais.com/produit/vis/slug") == ""

    def test_storage_state_nologin_ignore_la_session(self, tmp_path):
        """Passe 1 : la session est ignorée même présente (catalogue public)."""
        from scrapers.Legallais_P1.products.methode_sitemap import storage_state_a_amorcer
        chemin = tmp_path / "session.json"
        assert storage_state_a_amorcer(False, True, chemin) is None

    def test_storage_state_logue(self, tmp_path):
        from scrapers.Legallais_P1.products.methode_sitemap import storage_state_a_amorcer
        chemin = tmp_path / "session.json"
        assert storage_state_a_amorcer(True, True, chemin) == chemin
        assert storage_state_a_amorcer(True, False, chemin) is None

    def test_categories_retire_accueil(self):
        from scrapers.Legallais_P1.products.legallais_fiche_html import (
            categories_depuis_breadcrumb,
        )
        assert categories_depuis_breadcrumb(["Accueil", "Accueil", "Outillage"]) == ["Outillage"]

    def test_articles_codes_dedupliques(self):
        from scrapers.Legallais_P1.products.legallais_fiche_html import articles_codes
        assert articles_codes(_HTML_LEGALLAIS) == ["104802", "104803"]

    def test_parser_fiche(self):
        from scrapers.Legallais_P1.products.legallais_fiche_html import parser_fiche
        extrait = parser_fiche(_HTML_LEGALLAIS, "https://www.legallais.com/produit/vis/12345")
        assert extrait["designation"] == "Embout TX20"
        assert extrait["marque"] == "MILWAUKEE"
        assert extrait["ean"] == "3660000000001"
        assert extrait["ref"] == "12345"
        assert extrait["images"] == ["https://img/1.jpg", "https://img/2.jpg"]
        assert extrait["conditionnement"] == "Boîte de 10"
        assert extrait["ref_fabricant"] == "4932430859"
        assert extrait["docs"] == ["https://doc/fiche.pdf"]
        assert extrait["categories"] == ["Outillage", "Embouts"]
        # Prix et stock viennent de l'enrichissement, jamais du HTML statique.
        assert extrait["prix"] == ""
        assert extrait["stock"] == ""

    def test_mapper_article_prend_le_dessus(self):
        """L'article est l'unité vendable : sa réf et son prix priment sur la page."""
        from scrapers.Legallais_P1.products.legallais_article_infos import mapper_article
        base = {"url": "https://www.legallais.com/produit/vis/12345", "ref": "12345",
                "designation": "Gamme", "marque": "", "categories": ["Outillage"],
                "images": ["i.jpg"]}
        result = {"code": "104802", "title": "Embout TX20 SHW", "brand_title": "MILWAUKEE",
                  "price": {"net_price": 16.74}, "slug": "/produit/embout/89464/104802"}
        extrait = mapper_article(result, base)
        assert extrait["ref"] == "104802"
        assert extrait["prix"] == "16.74"
        assert extrait["designation"] == "Embout TX20 SHW"
        assert extrait["url"].endswith("/produit/embout/89464/104802")
        assert extrait["images"] == ["i.jpg"]  # conservé de la page

    def test_mapper_article_sans_prix(self):
        from scrapers.Legallais_P1.products.legallais_article_infos import mapper_article
        extrait = mapper_article({"code": "1", "price": {}}, {"url": "u", "categories": []})
        assert extrait["prix"] == ""

    def test_ligne_produit_valide(self):
        from scrapers.Legallais_P1.products.legallais_fiche_html import parser_fiche
        extrait = parser_fiche(_HTML_LEGALLAIS, "https://www.legallais.com/produit/vis/12345")
        ligne = element_produit(extrait, "P1")
        assert colonnes_inconnues(ligne) == set()
        assert not COLONNES_DECLINAISON & set(ligne)
