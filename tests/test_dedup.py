"""
Tests unitaires de la déduplication des fiches produit.

  core/dedup.py     — normalisation d'URL, clé d'identité, fusion, garde-fou
  db/mariadb_db.py  — save_product : insertion, enrichissement, idempotence

Les tests de save_product utilisent une fausse connexion : ils vérifient la
LOGIQUE (recherche préalable, colonnes réécrites, non-régression des valeurs
existantes) sans jamais toucher la base MariaDB.

Lancement : pytest tests/test_dedup.py -v
"""

import sys
from pathlib import Path

import pymysql
import pytest

# ── racine du projet sur sys.path ─────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.config import CSV_HEADERS  # noqa: E402
from core.dedup import (  # noqa: E402
    COLONNE_UID,
    CRITERES_PAR_SITE,
    champs_modifies,
    cle_naturelle,
    criteres_du_site,
    fusionner,
    normaliser_ean,
    normaliser_reference,
    normaliser_texte,
    normaliser_url,
    score_completude,
    sont_jumelles,
    uid_produit,
)


def _row(**champs) -> dict:
    """Ligne produit complète (toutes les colonnes CSV_HEADERS présentes)."""
    ligne = dict.fromkeys(CSV_HEADERS, "")
    ligne.update(champs)
    return ligne


# ═══════════════════════════════════════════════════════════════════════════════
# NORMALISATION D'URL
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormaliserUrl:

    def test_fragment_supprime(self):
        """Sider publie la même fiche avec et sans ancre #reference."""
        avec = "https://www.sider.biz/produit/mitigeur.153713594#246390"
        sans = "https://www.sider.biz/produit/mitigeur.153713594"
        assert normaliser_url(avec) == normaliser_url(sans)

    def test_slash_final_ignore(self):
        assert normaliser_url("https://a.fr/p/1/") == normaliser_url("https://a.fr/p/1")

    def test_casse_hote_ignoree(self):
        assert normaliser_url("HTTPS://WWW.A.FR/p") == normaliser_url("https://www.a.fr/p")

    def test_parametres_de_tracking_retires(self):
        avec = "https://a.fr/p?utm_source=mail&gclid=xyz"
        assert normaliser_url(avec) == normaliser_url("https://a.fr/p")

    def test_parametre_metier_conserve(self):
        """?idvar= identifie la déclinaison chez Setin : le perdre fusionnerait
        des articles distincts."""
        a = "https://www.setin.fr/kit-a8635.html?idvar=BLU293"
        b = "https://www.setin.fr/kit-a8635.html?idvar=BLU294"
        assert normaliser_url(a) != normaliser_url(b)

    def test_ordre_des_parametres_sans_effet(self):
        a = "https://a.fr/p?b=2&a=1"
        b = "https://a.fr/p?a=1&b=2"
        assert normaliser_url(a) == normaliser_url(b)

    def test_url_vide(self):
        assert normaliser_url("") == ""
        assert normaliser_url(None) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# NORMALISATIONS SIMPLES
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalisations:

    def test_reference_espaces_et_casse(self):
        assert normaliser_reference(" blu 293 ") == "BLU293"

    def test_reference_vide(self):
        assert normaliser_reference(None) == ""

    def test_ean_ne_garde_que_les_chiffres(self):
        assert normaliser_ean("  9009494117592 ") == "9009494117592"
        assert normaliser_ean("EAN: 3-456") == "3456"

    def test_ean_nul_considere_absent(self):
        assert normaliser_ean("0000000000000") == ""
        assert normaliser_ean("") == ""

    def test_texte_sans_accent_ni_casse(self):
        assert normaliser_texte("  Mitigeur  THERMOSTATIQUE ") == "mitigeur thermostatique"
        assert normaliser_texte("Réf. Écologique") == normaliser_texte("ref. ecologique")


# ═══════════════════════════════════════════════════════════════════════════════
# CLÉ D'IDENTITÉ
# ═══════════════════════════════════════════════════════════════════════════════

class TestIdentite:

    def test_tous_les_sites_sont_configures(self):
        """Un site oublié retomberait silencieusement sur les critères par défaut."""
        from db.mariadb_db import SITE_PREFIX
        assert set(SITE_PREFIX) <= set(CRITERES_PAR_SITE)

    def test_sonepar_identifie_par_reference(self):
        assert criteres_du_site("sonepar")[0] == "ref"

    def test_sites_a_reference_contaminee_identifies_par_url(self):
        """Legallais et Sider écrivent la réf. du parent sur les déclinaisons :
        dédoublonner par référence y fusionnerait des articles différents."""
        assert criteres_du_site("legallais") == ("url",)
        assert criteres_du_site("sider") == ("url",)

    def test_meme_fiche_meme_uid(self):
        a = _row(product_fournisseur_url="https://www.sider.biz/p/x.123#456",
                 product_price_ht="10.00")
        b = _row(product_fournisseur_url="https://www.sider.biz/p/x.123",
                 product_price_ht="12.50")
        assert uid_produit("sider", a) == uid_produit("sider", b)

    def test_fiches_differentes_uid_differents(self):
        a = _row(product_fournisseur_url="https://www.sider.biz/p/x.123")
        b = _row(product_fournisseur_url="https://www.sider.biz/p/y.456")
        assert uid_produit("sider", a) != uid_produit("sider", b)

    def test_uid_isole_les_sites(self):
        """Deux fournisseurs peuvent servir la même URL sans se télescoper."""
        row = _row(product_fournisseur_url="https://a.fr/p")
        assert uid_produit("sider", row) != uid_produit("prolians", row)

    def test_sonepar_deux_urls_une_seule_fiche(self):
        """Sonepar expose /products/<slug>-<ref> ET /products/<ref>."""
        a = _row(product_reference_fournisseur="05594004005",
                 product_fournisseur_url="https://www.sonepar.fr/catalog/fr-fr/products/cable-05594004005")
        b = _row(product_reference_fournisseur="05594004005",
                 product_fournisseur_url="https://www.sonepar.fr/catalog/fr-fr/products/05594004005")
        assert uid_produit("sonepar", a) == uid_produit("sonepar", b)

    def test_repli_sur_le_critere_suivant(self):
        """Sans référence, Sonepar retombe sur l'URL plutôt que de perdre la ligne."""
        row = _row(product_fournisseur_url="https://www.sonepar.fr/p/1")
        assert "|url|" in cle_naturelle("sonepar", row)
        assert uid_produit("sonepar", row) is not None

    def test_ligne_sans_identifiant(self):
        """Aucun critère renseigné → pas d'uid (colonne NULL, jamais en conflit)."""
        assert uid_produit("sider", _row(product_designation="Sans URL")) is None

    def test_uid_est_un_sha1(self):
        uid = uid_produit("sider", _row(product_fournisseur_url="https://a.fr/p"))
        assert len(uid) == 40
        assert all(c in "0123456789abcdef" for c in uid)

    def test_criteres_forces(self):
        row = _row(product_reference_fournisseur="ABC",
                   product_fournisseur_url="https://a.fr/p")
        assert cle_naturelle("sider", row, ("ref",)) == "sider|ref|ABC"

    def test_critere_inconnu_leve(self):
        with pytest.raises(ValueError):
            cle_naturelle("sider", _row(), ("couleur",))


# ═══════════════════════════════════════════════════════════════════════════════
# FUSION NON DESTRUCTIVE
# ═══════════════════════════════════════════════════════════════════════════════

class TestFusion:

    def test_valeur_vide_n_ecrase_jamais(self):
        base = _row(product_designation="Mitigeur", product_ean="123")
        neuve = _row(product_designation="", product_ean="")
        assert champs_modifies(base, neuve, CSV_HEADERS) == {}

    def test_valeur_vide_completee(self):
        base = _row(product_designation="Mitigeur")
        neuve = _row(product_ean="123")
        assert champs_modifies(base, neuve, CSV_HEADERS) == {"product_ean": "123"}

    def test_prix_rafraichi(self):
        base = _row(product_price_ht="10.00")
        neuve = _row(product_price_ht="12.50")
        assert champs_modifies(base, neuve, CSV_HEADERS) == {"product_price_ht": "12.50"}

    def test_colonne_figee_jamais_reecrite(self):
        base = _row(product_fournisseur_url="https://a.fr/1")
        neuve = _row(product_fournisseur_url="https://a.fr/1?utm_source=x")
        maj = champs_modifies(base, neuve, CSV_HEADERS,
                              figees=frozenset({"product_fournisseur_url"}))
        assert "product_fournisseur_url" not in maj

    def test_fusion_conserve_les_deux_apports(self):
        base = _row(product_designation="Mitigeur", product_ean="")
        neuve = _row(product_designation="", product_ean="123", product_price_ht="9.90")
        fusion = fusionner(base, neuve, CSV_HEADERS)
        assert fusion["product_designation"] == "Mitigeur"
        assert fusion["product_ean"] == "123"
        assert fusion["product_price_ht"] == "9.90"

    def test_score_completude_ordonne_les_survivants(self):
        riche = _row(product_designation="Mitigeur thermostatique",
                     product_ean="123", product_description="Longue description")
        pauvre = _row(product_designation="Mitigeur")
        assert score_completude(riche, CSV_HEADERS) > score_completude(pauvre, CSV_HEADERS)


# ═══════════════════════════════════════════════════════════════════════════════
# GARDE-FOU DU MODE STRICT
# ═══════════════════════════════════════════════════════════════════════════════

class TestGardeFou:

    def test_ean_identique_suffit(self):
        a = _row(product_ean="9009494117592", product_designation="Attache A")
        b = _row(product_ean="9009494117592", product_designation="Attache B")
        assert sont_jumelles(a, b)

    def test_ean_different_refuse(self):
        a = _row(product_ean="111", product_designation="Attache")
        b = _row(product_ean="222", product_designation="Attache")
        assert not sont_jumelles(a, b)

    def test_reference_differente_refuse(self):
        a = _row(product_reference_fournisseur="422980")
        b = _row(product_reference_fournisseur="422930")
        assert not sont_jumelles(a, b)

    def test_designation_differente_refuse(self):
        """Le cas Legallais : même référence, deux cylindres de tailles différentes."""
        a = _row(product_reference_fournisseur="576458",
                 product_designation="Cylindre Chausey II 45 x 50 mm LN")
        b = _row(product_reference_fournisseur="576458",
                 product_designation="Cylindre Chausey II 30x50 LN")
        assert not sont_jumelles(a, b)

    def test_titre_rallonge_tolere(self):
        """Sider suffixe parfois la marque en double dans le titre."""
        a = _row(product_designation="Disjoncteur courbe D-25 A - Legrand LEGRAND")
        b = _row(product_designation="Disjoncteur courbe D-25 A - Legrand")
        assert sont_jumelles(a, b)

    def test_prix_different_ne_refuse_pas(self):
        """Le prix est volatil : c'est ce que la fusion doit rafraîchir."""
        a = _row(product_reference_fournisseur="475201", product_price_ht="1.61")
        b = _row(product_reference_fournisseur="475201", product_price_ht="1.73")
        assert sont_jumelles(a, b)


# ═══════════════════════════════════════════════════════════════════════════════
# save_product — logique d'écriture (fausse connexion, aucune base réelle)
# ═══════════════════════════════════════════════════════════════════════════════

class FauxCurseur:
    """Curseur minimal : rejoue une ligne existante puis enregistre les requêtes.

    ``erreurs`` fait échouer les N premières écritures avec une IntegrityError,
    pour éprouver le repli de save_product sur un conflit d'unicité.
    """

    def __init__(self, existante=None, erreurs: int = 0, apres_erreur=None):
        self._existante = existante  # tuple (id, uid, *CSV_HEADERS) ou None
        self._erreurs = erreurs
        self._apres_erreur = apres_erreur
        self.requetes: list[tuple[str, list]] = []
        self._dernier = None

    def execute(self, sql, params=None):
        self.requetes.append((sql, list(params or [])))
        if sql.lstrip().startswith("SELECT"):
            self._dernier = self._existante
            return
        self._dernier = None
        if self._erreurs > 0:
            self._erreurs -= 1
            if self._apres_erreur is not None:
                self._existante = self._apres_erreur
            raise pymysql.IntegrityError(1062, "Duplicate entry")

    def fetchone(self):
        return self._dernier

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FausseConnexion:
    def __init__(self, curseur):
        self._curseur = curseur
        self.commits = 0

    def cursor(self):
        return self._curseur

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def sql_capture(monkeypatch):
    """Remplace _get_conn par une fausse connexion pilotable."""
    import db.mariadb_db as mdb

    def _installer(existante=None, erreurs=0, apres_erreur=None):
        curseur = FauxCurseur(existante, erreurs, apres_erreur)
        monkeypatch.setattr(mdb, "_get_conn", lambda: FausseConnexion(curseur))
        return curseur

    return _installer


def _ligne_existante(row_id: int, uid: str | None, **champs) -> tuple:
    ligne = _row(**champs)
    return (row_id, uid, *[ligne[h] for h in CSV_HEADERS])


class TestSaveProduct:

    def test_fiche_inconnue_inseree_avec_uid(self, sql_capture):
        from db.mariadb_db import COL_ETAT, COL_VUE, ETAT_ACTIF, save_product
        curseur = sql_capture(existante=None)
        row = _row(product_fournisseur_url="https://www.sider.biz/p/x.1",
                   product_designation="Mitigeur")

        assert save_product(None, "sider", row) == "insert"

        inserts = [(s, p) for s, p in curseur.requetes if s.lstrip().startswith("INSERT")]
        assert len(inserts) == 1
        sql, params = inserts[0]
        assert COLONNE_UID in sql
        assert uid_produit("sider", row) in params
        # La fiche neuve est datée et marquée active dans le MÊME insert : suivre
        # le cycle de vie ne doit rien coûter de plus qu'avant.
        assert COL_VUE in sql
        assert ETAT_ACTIF in params
        assert COL_ETAT in sql

    def test_fiche_connue_non_dupliquee(self, sql_capture):
        from db.mariadb_db import save_product
        row = _row(product_fournisseur_url="https://www.sider.biz/p/x.1",
                   product_designation="Mitigeur", product_price_ht="12.50")
        existante = _ligne_existante(7, uid_produit("sider", row),
                                     product_fournisseur_url="https://www.sider.biz/p/x.1",
                                     product_designation="Mitigeur",
                                     product_price_ht="10.00")
        curseur = sql_capture(existante=existante)

        assert save_product(None, "sider", row) == "update"

        assert not [s for s, _ in curseur.requetes if s.lstrip().startswith("INSERT")]
        maj = [(s, p) for s, p in curseur.requetes if s.lstrip().startswith("UPDATE")]
        assert len(maj) == 1
        sql, params = maj[0]
        assert "product_price_ht" in sql
        assert "12.50" in params
        assert params[-1] == 7  # WHERE id = 7

    def test_rescrape_identique_n_ecrit_rien(self, sql_capture):
        """Idempotence : rejouer un scrape ne touche pas la base."""
        from db.mariadb_db import save_product
        row = _row(product_fournisseur_url="https://www.sider.biz/p/x.1",
                   product_designation="Mitigeur")
        curseur = sql_capture(existante=_ligne_existante(
            7, uid_produit("sider", row),
            product_fournisseur_url="https://www.sider.biz/p/x.1",
            product_designation="Mitigeur"))

        assert save_product(None, "sider", row) == "inchange"
        assert not [s for s, _ in curseur.requetes
                    if s.lstrip().startswith(("INSERT", "UPDATE"))]

    def test_url_canonique_jamais_reecrite(self, sql_capture):
        """L'URL porte un index UNIQUE : la réécrire collisionnerait."""
        from db.mariadb_db import save_product
        row = _row(product_fournisseur_url="https://www.sider.biz/p/x.1#999",
                   product_designation="Mitigeur bis")
        curseur = sql_capture(existante=_ligne_existante(
            7, uid_produit("sider", row),
            product_fournisseur_url="https://www.sider.biz/p/x.1",
            product_designation="Mitigeur"))

        save_product(None, "sider", row)

        maj = [s for s, _ in curseur.requetes if s.lstrip().startswith("UPDATE")]
        assert maj
        assert "product_fournisseur_url" not in maj[0]

    def test_champ_vide_n_efface_pas_l_existant(self, sql_capture):
        from db.mariadb_db import save_product
        row = _row(product_fournisseur_url="https://www.sider.biz/p/x.1")
        curseur = sql_capture(existante=_ligne_existante(
            7, uid_produit("sider", row),
            product_fournisseur_url="https://www.sider.biz/p/x.1",
            product_designation="Mitigeur", product_description="Longue"))

        assert save_product(None, "sider", row) == "inchange"
        assert not [s for s, _ in curseur.requetes if s.lstrip().startswith("UPDATE")]

    def test_conflit_d_unicite_bascule_en_enrichissement(self, sql_capture):
        """Scrapes concurrents : l'INSERT refusé ne doit pas faire échouer le scrape."""
        from db.mariadb_db import save_product
        row = _row(product_fournisseur_url="https://www.sider.biz/p/x.1",
                   product_designation="Mitigeur", product_price_ht="12.50")
        deja_creee = _ligne_existante(
            9, uid_produit("sider", row),
            product_fournisseur_url="https://www.sider.biz/p/x.1",
            product_designation="Mitigeur")
        curseur = sql_capture(existante=None, erreurs=1, apres_erreur=deja_creee)

        assert save_product(None, "sider", row) == "update"

        maj = [(s, p) for s, p in curseur.requetes if s.lstrip().startswith("UPDATE")]
        assert len(maj) == 1
        assert maj[0][1][-1] == 9  # enrichit la fiche créée entre-temps

    def test_upsert_product_cherche_par_reference(self, sql_capture):
        """Les scrapers « par références » visent une fiche connue par sa réf."""
        from db.mariadb_db import upsert_product
        curseur = sql_capture(existante=None)
        upsert_product(None, "prolians",
                       _row(product_reference_fournisseur="10405386"))

        conditions = [s for s, _ in curseur.requetes if s.lstrip().startswith("SELECT")]
        assert any("product_reference_fournisseur" in s for s in conditions)


# ═══════════════════════════════════════════════════════════════════════════════
# PASSE AUTOMATIQUE DE FIN DE SCRAPE
# ═══════════════════════════════════════════════════════════════════════════════

class TestApresScrape:

    def test_echec_silencieux(self, monkeypatch):
        """Un incident de dédoublonnage ne doit pas faire échouer un scrape réussi."""
        import db.mariadb_db as mdb

        def _boum(_site):
            raise ConnectionError("base injoignable")

        monkeypatch.setattr(mdb, "_ensure_tables", _boum)
        assert mdb.dedupliquer_apres_scrape("sider") is None

    def test_mode_strict_et_ecriture(self, monkeypatch):
        """La passe automatique applique les fusions, mais jamais les cas ambigus."""
        import db.mariadb_db as mdb
        appels = {}

        monkeypatch.setattr(mdb, "_ensure_tables", lambda _s: None)
        monkeypatch.setattr(mdb, "deduplicate_products",
                            lambda site, **kw: appels.update(site=site, **kw) or {
                                "lignes": 10, "lignes_supprimees": 0, "conflits": 0})

        mdb.dedupliquer_apres_scrape("sonepar")
        assert appels == {"site": "sonepar", "apply": True, "strict": True}

    def test_gui_couvre_toutes_les_actions_produits(self):
        """Une nouvelle action écrivant dans products doit être dédoublonnée."""
        from gui.interface import ScraperApp
        actions_produits = {
            "produits", "catalogue_complet", "catalogue_light_full",
            "maj_prixstock", "refs",
        }
        assert actions_produits <= ScraperApp._CLES_PRODUITS

    def test_gui_connait_tous_les_sites(self):
        """_SITE_KEYS doit couvrir les 5 fournisseurs, sinon la passe est sautée."""
        from db.mariadb_db import SITE_PREFIX
        from gui.interface import ScraperApp
        assert set(ScraperApp._SITE_KEYS.values()) == set(SITE_PREFIX)
