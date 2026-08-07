"""Persistance MariaDB — API identique à db/sqlite_db.py.

Remplace sqlite_db dans les scrapers : changer l'import suffit.

Tables cibles dans Scraper_base :
  {PREFIX}_products  /  {PREFIX}_orders  /  {PREFIX}_tracking
  où PREFIX = P1 (legallais), P3 (prolians), P5 (setin), P6 (sider)

Unicité des fiches produit
--------------------------
Chaque ligne de {PREFIX}_products porte un ``product_uid`` (SHA-1 de sa clé
d'identité, cf. core/dedup.py) sous index UNIQUE : une fiche déjà connue est
ENRICHIE, jamais dupliquée. Tout passe par ``save_product`` ; les anciennes
fonctions d'insertion y délèguent.

API publique
------------
init_site_db(site)                        → connexion (inutilisée, kept for compat)
save_product(conn, site, row)             → "insert" | "update" | "inchange"
insert_product(conn, site, row)
insert_order(conn, site, row)
insert_tracking(conn, site, row)
get_scraped_product_urls(conn, site)      → set[str]
deduplicate_products(site, apply=False)   → rapport dict (dédoublonnage rétroactif)
export_table_to_csv(conn, table, headers, out_path) → int
"""

from __future__ import annotations

import csv
import logging
import os
import time
from collections import defaultdict
from datetime import date as _date, datetime as _datetime
from pathlib import Path

import pymysql
from dotenv import load_dotenv

from core.config import CSV_HEADERS, ORDERS_CSV_HEADERS, TRACKING_CSV_HEADERS
from core.dedup import (
    COLONNE_UID,
    champs_modifies,
    criteres_du_site,
    normaliser_url,
    score_completude,
    sont_jumelles,
    uid_produit,
)

load_dotenv()

_log = logging.getLogger(__name__)

# ─── Mapping site → préfixe de table ─────────────────────────────────────────

SITE_PREFIX: dict[str, str] = {
    "legallais": "P1",
    "prolians":  "P3",
    "setin":     "P5",
    "sonepar":   "P8",
    "sider":     "P6",
}

# ─── Paramètres de connexion ──────────────────────────────────────────────────

def _conn_params() -> dict:
    return {
        'host': os.getenv("DB_HOST"),
        'port': int(os.getenv("DB_PORT", 3306)),
        'user': os.getenv("DB_USER"),
        'password': os.getenv("DB_PASSWORD"),
        'database': os.getenv("DB_NAME"),
        'charset': "utf8mb4",
        'connect_timeout': 10,
        'autocommit': False,
    }


def _get_conn() -> pymysql.connections.Connection:
    """Ouvre une connexion PyMySQL avec retry automatique (3 tentatives)."""
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            conn = pymysql.connect(**_conn_params())
            return conn
        except pymysql.Error as e:
            last_err = e
            _log.warning("Connexion MariaDB échouée (tentative %d/3) : %s", attempt, e)
            time.sleep(attempt * 2)
    raise ConnectionError(f"Impossible de se connecter à MariaDB après 3 tentatives : {last_err}")


# ─── Sentinel de compatibilité ───────────────────────────────────────────────

class _ConnSentinel:
    """Remplace sqlite3.Connection dans les scrapers existants.

    Truthy → les guards `if db_conn:` passent.
    `.close()` → no-op.
    """
    def close(self) -> None:
        pass  # comment explaining why the method is empty

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        return "<MariaDB sentinel>"


_SENTINEL = _ConnSentinel()


# Sites pour lesquels seule la table products est nécessaire
_PRODUCTS_ONLY_SITES: frozenset[str] = frozenset({"sonepar"})

# L'URL de fiche identifie une page unique chez TOUS les fournisseurs scrapés :
# la déclinaison est portée par l'URL elle-même (?idvar= chez Setin, segment
# final chez Legallais). L'unicité fine est assurée par product_uid.
_PRODUCTS_URL_UNIQUE: frozenset[str] = frozenset(SITE_PREFIX)


# ─── Création des tables ──────────────────────────────────────────────────────

def _a_index_unique(cur, table: str, colonne: str) -> bool:
    """True si une contrainte UNIQUE couvre déjà ``colonne`` (quel que soit son nom).

    Les tables de production portent des index posés à la main sous des noms
    hétérogènes (``uq_P1_products``, ``P5_products_UNIQUE``,
    ``product_fournisseur_url``…) : on teste la colonne, pas le nom, pour ne
    pas empiler d'index redondants.
    """
    cur.execute(f"SHOW INDEX FROM `{table}`")
    for ligne in cur.fetchall():
        non_unique, _nom, seq, col = ligne[1], ligne[2], ligne[3], ligne[4]
        if col == colonne and int(non_unique) == 0 and int(seq) == 1:
            return True
    return False


# ─── Cycle de vie des articles ────────────────────────────────────────────────

#: Colonnes de suivi du cycle de vie, hors CSV_HEADERS (comme product_uid).
COL_VUE = "derniere_vue_le"
COL_ETAT = "etat_fournisseur"
COL_ABSENCES = "absences_consecutives"
COL_RETIRE_LE = "retire_le"
COL_VERIFIE_LE = "verifie_le"

ETAT_ACTIF = "actif"
ETAT_DOUTEUX = "douteux"
ETAT_RETIRE = "retire"

#: Libellé écrit dans product_stock_status sur un retrait confirmé.
#: ⚠️ Le mapping « texte → quantité » du PIM doit le faire tomber à 0. On choisit
#: un libellé EXPLICITE plutôt que le vocabulaire de chaque fournisseur : « PAS EN
#: STOCK » signifierait « en rupture », pas « n'existe plus ».
LIBELLE_STOCK_RETIRE = "RETIRE DU CATALOGUE"

#: Colonnes ajoutées aux tables produits, avec leur définition SQL.
_COLONNES_CYCLE_VIE: dict[str, str] = {
    COL_VUE: "DATETIME NULL",
    COL_ETAT: "VARCHAR(16) NULL",
    COL_ABSENCES: "INT NOT NULL DEFAULT 0",
    COL_RETIRE_LE: "DATETIME NULL",
    COL_VERIFIE_LE: "DATETIME NULL",
}


def _ensure_cycle_vie(cur, table: str) -> None:
    """Ajoute les colonnes de cycle de vie si elles manquent (idempotent)."""
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    presentes = {ligne[0] for ligne in cur.fetchall()}
    for colonne, definition in _COLONNES_CYCLE_VIE.items():
        if colonne not in presentes:
            cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{colonne}` {definition}")
            _log.info("Colonne %s ajoutée à %s", colonne, table)
    # Un balayage cherche « les fiches non vues depuis X » : sans index, c'est un
    # parcours complet de 100 000 lignes à chaque run.
    if not _a_index(cur, table, COL_VUE):
        try:
            cur.execute(f"ALTER TABLE `{table}` ADD INDEX `idx_{table}_vue` (`{COL_VUE}`)")
        except pymysql.Error as e:
            _log.warning("Index sur %s.%s non créé : %s", table, COL_VUE, e)


def _a_index(cur, table: str, colonne: str) -> bool:
    """True si une colonne porte un index, unique ou non."""
    cur.execute(f"SHOW INDEX FROM `{table}`")
    return any(ligne[4] == colonne for ligne in cur.fetchall())


def _ensure_table_runs(cur) -> None:
    """Crée le journal des runs de scraping.

    ⚠️ C'est la **pièce de sûreté** de tout le mécanisme : seul un run marqué
    ``complet`` autorise un balayage. Un run limité, incrémental, confiné à une
    catégorie ou interrompu ne doit JAMAIS conclure à une absence — sinon un
    crash à 30 % marque 70 % du catalogue comme retiré.
    """
    cur.execute(
        "CREATE TABLE IF NOT EXISTS `runs_scrape` ("
        "  `id` INT AUTO_INCREMENT PRIMARY KEY,"
        "  `fournisseur` VARCHAR(32) NOT NULL,"
        "  `methode` VARCHAR(32) NULL,"
        "  `debut` DATETIME NOT NULL,"
        "  `fin` DATETIME NULL,"
        "  `complet` TINYINT(1) NOT NULL DEFAULT 0,"
        "  `enumerees` INT NULL,"
        "  `ecrites` INT NULL,"
        "  `note` TEXT NULL,"
        "  INDEX `idx_runs_fournisseur` (`fournisseur`, `debut`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )


def _ensure_unicite_produits(cur, table: str) -> None:
    """Ajoute la colonne product_uid et son index UNIQUE si nécessaire.

    L'index se pose sans risque même sur une table peuplée : les uid des
    lignes existantes valent NULL, et NULL n'entre jamais en conflit dans un
    index UNIQUE MariaDB. C'est ``deduplicate_products`` qui les renseigne
    ensuite, après avoir fusionné les doublons hérités.
    """
    cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (COLONNE_UID,))
    if cur.fetchone() is None:
        cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{COLONNE_UID}` CHAR(40) NULL")
        _log.info("Colonne %s ajoutée à %s", COLONNE_UID, table)

    if not _a_index_unique(cur, table, COLONNE_UID):
        try:
            cur.execute(
                f"ALTER TABLE `{table}` ADD UNIQUE KEY `uq_{table}_uid` (`{COLONNE_UID}`)"
            )
            _log.info("Index UNIQUE uq_%s_uid créé", table)
        except pymysql.Error as e:
            _log.warning(
                "Index UNIQUE sur %s.%s non créé (%s) — lancer : python dedoublonnage.py --apply",
                table, COLONNE_UID, e,
            )


def _ensure_tables(site: str) -> None:
    """Crée les tables nécessaires et la séquence de déclinaisons du site."""
    prefix = SITE_PREFIX[site]
    products_url_unique = "product_fournisseur_url" if site in _PRODUCTS_URL_UNIQUE else None
    all_kinds = [
        ("products", CSV_HEADERS,          products_url_unique),
        ("orders",   ORDERS_CSV_HEADERS,   "id_cmd"),
        ("tracking", TRACKING_CSV_HEADERS, "id_cmd"),
    ]
    kinds = [all_kinds[0]] if site in _PRODUCTS_ONLY_SITES else all_kinds
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            for kind, headers, unique_col in kinds:
                table = f"{prefix}_{kind}"
                col_defs = ",\n    ".join(f"`{h}` TEXT" for h in headers)
                unique_clause = f",\n    UNIQUE KEY `uq_{table}` (`{unique_col}`(255))" if unique_col else ""
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS `{table}` (\n"
                    f"    `id` INT AUTO_INCREMENT PRIMARY KEY,\n"
                    f"    {col_defs}{unique_clause}\n"
                    f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
                )
                # Ajoute la contrainte UNIQUE si la table existait déjà sans elle
                if unique_col and not _a_index_unique(cur, table, unique_col):
                    try:
                        cur.execute(
                            f"ALTER TABLE `{table}` ADD UNIQUE KEY `uq_{table}` (`{unique_col}`(255))"
                        )
                    except pymysql.Error as e:
                        _log.warning("UNIQUE %s.%s non posée : %s", table, unique_col, e)
                if kind == "products":
                    _ensure_unicite_produits(cur, table)
                    _ensure_cycle_vie(cur, table)
                _log.debug("Table prête : %s", table)
            _ensure_table_runs(cur)
            # Séquence globale d'index pour les groupes de déclinaisons
            cur.execute(
                f"CREATE SEQUENCE IF NOT EXISTS `seq_decli_{prefix}`"
                " START WITH 1 INCREMENT BY 1 CACHE 100 NOCYCLE"
            )
            _log.debug("Séquence prête : seq_decli_%s", prefix)
        conn.commit()
    finally:
        conn.close()


# ─── Initialisation publique (compat sqlite_db) ───────────────────────────────

def init_site_db(site: str) -> _ConnSentinel:
    """Vérifie la connexion MariaDB et crée les tables si nécessaire.

    Lève ValueError si le site est inconnu, ConnectionError si la DB est injoignable.
    """
    if site not in SITE_PREFIX:
        raise ValueError(f"Unknown site: {site!r}. Choose from {list(SITE_PREFIX)}")
    _ensure_tables(site)
    _log.info("init_site_db(%s) OK — tables prêtes", site)
    return _SENTINEL


# ─── Helpers internes ─────────────────────────────────────────────────────────

def _table(site: str, kind: str) -> str:
    prefix = SITE_PREFIX.get(site)
    if prefix is None:
        raise ValueError(f"Unknown site: {site!r}")
    return f"{prefix}_{kind}"


def _insert(
    site: str,
    kind: str,
    headers: list[str],
    row: dict,
    conflict: str = "IGNORE",
) -> None:
    """INSERT générique avec stratégie de conflit (IGNORE ou REPLACE)."""
    table  = _table(site, kind)
    cols   = ", ".join(f"`{h}`" for h in headers)
    ph     = ", ".join("%s" for _ in headers)
    values = [str(row.get(h, "") or "") for h in headers]

    if conflict == "REPLACE":
        updates = ", ".join(f"`{h}`=VALUES(`{h}`)" for h in headers)
        sql = f"INSERT INTO `{table}` ({cols}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {updates}"
    else:
        sql = f"INSERT IGNORE INTO `{table}` ({cols}) VALUES ({ph})"

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, values)
        conn.commit()
    except pymysql.Error as e:
        conn.rollback()
        _log.exception("_insert(%s, %s) échec : %s | row=%s", site, kind, e,
                   row.get("id_cmd") or row.get("product_fournisseur_url", "?"))
        raise
    finally:
        conn.close()


# ─── Écriture des produits : point d'entrée unique ────────────────────────────

#: Colonnes jamais réécrites sur une fiche existante. L'URL porte un index
#: UNIQUE : la remplacer par l'URL alias d'un doublon provoquerait une
#: collision avec une autre ligne. La première URL vue fait foi.
_COLONNES_FIGEES: frozenset[str] = frozenset({"product_fournisseur_url"})

#: Ordre de recherche d'une fiche déjà en base, par clé.
_CLES_DEFAUT: tuple[str, ...] = ("uid", "url")


def _chercher_fiche(cur, table: str, site: str, row: dict,
                    cles: tuple[str, ...]) -> tuple[int, dict] | None:
    """Retrouve la fiche existante correspondant à ``row``, ou None.

    Essaie les clés dans l'ordre demandé et s'arrête à la première trouvée.
    Renvoie (id, ligne complète) pour permettre une fusion non destructive.
    """
    colonnes = ", ".join(f"`{h}`" for h in CSV_HEADERS)
    for cle in cles:
        if cle == "uid":
            valeur = uid_produit(site, row)
            condition = f"`{COLONNE_UID}` = %s"
        elif cle == "url":
            valeur = str(row.get("product_fournisseur_url", "") or "").strip()
            condition = "`product_fournisseur_url` = %s"
        elif cle == "ref":
            valeur = str(row.get("product_reference_fournisseur", "") or "").strip()
            condition = "`product_reference_fournisseur` = %s"
        else:
            raise ValueError(f"Clé de recherche inconnue : {cle!r}")
        if not valeur:
            continue
        cur.execute(
            f"SELECT `id`, `{COLONNE_UID}`, {colonnes} FROM `{table}` "
            f"WHERE {condition} LIMIT 1",
            (valeur,),
        )
        trouve = cur.fetchone()
        if trouve:
            existante = dict(zip(CSV_HEADERS, trouve[2:]))
            existante[COLONNE_UID] = trouve[1]
            return trouve[0], existante
    return None


def save_product(_conn, site: str, row: dict,
                 cles: tuple[str, ...] = _CLES_DEFAUT) -> str:
    """Écrit une fiche produit SANS jamais créer de doublon.

    C'est le point d'entrée unique de l'écriture produit : toutes les autres
    fonctions y délèguent. Le comportement est idempotent — rejouer un scrape
    n'ajoute aucune ligne, il enrichit les fiches connues.

    Identification : ``product_uid`` (cf. core/dedup.py), avec repli sur les
    clés de ``cles``. Fusion NON DESTRUCTIVE : une valeur vide du nouveau
    scrape n'efface jamais une valeur déjà en base ; une valeur différente et
    non vide (prix, stock…) rafraîchit la fiche.

    Args:
        _conn: ignoré (compat sqlite_db).
        site: « legallais », « prolians », « setin », « sider », « sonepar ».
        row: ligne au format CSV_HEADERS.
        cles: ordre de recherche de la fiche existante.

    Returns:
        « insert » (fiche nouvelle), « update » (fiche enrichie) ou
        « inchange » (rien à écrire).
    """
    table = _table(site, "products")
    uid = uid_produit(site, row)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            trouve = _chercher_fiche(cur, table, site, row, cles)

            if trouve is None:
                # ``derniere_vue_le`` part dans l'INSERT lui-même : marquer une
                # fiche neuve ne doit rien coûter de plus.
                colonnes = CSV_HEADERS + [COLONNE_UID, COL_ETAT]
                cols = ", ".join(f"`{h}`" for h in colonnes) + f", `{COL_VUE}`"
                ph = ", ".join("%s" for _ in colonnes) + ", NOW()"
                valeurs = [str(row.get(h, "") or "") for h in CSV_HEADERS] + [uid, ETAT_ACTIF]
                try:
                    cur.execute(
                        f"INSERT INTO `{table}` ({cols}) VALUES ({ph})", valeurs
                    )
                    conn.commit()
                    return "insert"
                except pymysql.IntegrityError:
                    # La fiche a été créée entre-temps (scrapes concurrents) ou
                    # existe sous une URL que les clés demandées ne couvraient
                    # pas : on bascule en enrichissement au lieu d'échouer.
                    conn.rollback()
                    trouve = _chercher_fiche(cur, table, site, row, ("uid", "url"))
                    if trouve is None:
                        _log.warning(
                            "save_product(%s) conflit d'unicité non résolu | url=%s",
                            site, row.get("product_fournisseur_url", "?"),
                        )
                        return "inchange"

            row_id, existante = trouve
            maj = champs_modifies(existante, row, CSV_HEADERS, _COLONNES_FIGEES)
            if uid and existante.get(COLONNE_UID) != uid:
                maj[COLONNE_UID] = uid
            if not maj:
                # Fiche retrouvée à l'identique : RIEN à réécrire, mais elle a
                # bel et bien été VUE. Le marquage part en lot plus tard — une
                # requête par fiche inchangée referait les 80 000 allers-retours
                # que le dédoublonnage a déjà payés une fois.
                marquer_vue(site, uid)
                return "inchange"

            # La fiche est réécrite de toute façon : le marquage voyage avec,
            # sans requête supplémentaire.
            marquage = (f", `{COL_VUE}` = NOW(), `{COL_ETAT}` = %s, "
                        f"`{COL_ABSENCES}` = 0")

            def _ecrire(colonnes: dict) -> None:
                clause = ", ".join(f"`{c}`=%s" for c in colonnes) + marquage
                cur.execute(
                    f"UPDATE `{table}` SET {clause} WHERE `id` = %s",
                    [*colonnes.values(), ETAT_ACTIF, row_id],
                )

            try:
                _ecrire(maj)
            except pymysql.IntegrityError:
                # L'uid calculé appartient déjà à une AUTRE ligne (doublon
                # hérité pas encore fusionné) : on enrichit sans toucher l'uid,
                # dedoublonnage.py réglera le conflit.
                conn.rollback()
                maj.pop(COLONNE_UID, None)
                if not maj:
                    marquer_vue(site, uid)
                    return "inchange"
                _ecrire(maj)
            conn.commit()
            return "update"
    except pymysql.Error as e:
        conn.rollback()
        _log.exception(
            "save_product(%s) échec : %s | url=%s", site, e,
            row.get("product_fournisseur_url", "?"),
        )
        raise
    finally:
        conn.close()


# ─── Insertion publique (compat : délèguent toutes à save_product) ────────────

def insert_product(_conn, site: str, row: dict) -> None:
    """Écrit une fiche produit sans créer de doublon (compat sqlite_db)."""
    save_product(_conn, site, row)


def upsert_product(_conn, site: str, row: dict) -> None:
    """Écrit une fiche identifiée en priorité par sa référence fournisseur.

    Utilisé par les scrapers « mise à jour par références » : la fiche visée
    est connue par sa référence, pas forcément par l'URL empruntée.
    Ordre de recherche : product_uid → référence → URL.
    """
    save_product(_conn, site, row, cles=("uid", "ref", "url"))


def upsert_product_by_url(_conn, site: str, row: dict) -> None:
    """Écrit une fiche identifiée par son URL (compat — délègue à save_product)."""
    save_product(_conn, site, row, cles=("uid", "url"))


def _is_valid_order_date(date_str: str) -> bool:
    s = (date_str or "").strip()
    if not s:
        return True
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            d = _datetime.strptime(s, fmt).date()
            return d <= _date.today()
        except ValueError:
            continue
    return False


def insert_order(_conn, site: str, row: dict) -> None:
    """Insère une commande ; ignore les doublons et dates invalides (compat sqlite_db)."""
    date_str = str(row.get("date_cmd", "") or "")
    if not _is_valid_order_date(date_str):
        _log.warning("insert_order(%s) rejeté — date invalide : %r", site, date_str)
        return
    _insert(site, "orders", ORDERS_CSV_HEADERS, row, conflict="IGNORE")


def insert_tracking(_conn, site: str, row: dict) -> None:
    """Insère ou remplace une ligne de suivi (compat sqlite_db)."""
    _insert(site, "tracking", TRACKING_CSV_HEADERS, row, conflict="REPLACE")


# ─── Reprise après interruption ───────────────────────────────────────────────

def get_scraped_product_urls(_conn, site: str) -> set[str]:
    """Retourne toutes les URL produit déjà en base (compat sqlite_db).

    L'ensemble contient chaque URL sous sa forme brute ET sous sa forme
    normalisée : le test ``url in seen`` des scrapers reconnaît ainsi une
    fiche déjà collectée même si le catalogue la présente cette fois avec un
    fragment ``#ref`` ou un paramètre de tracking en plus.
    """
    table = _table(site, "products")
    conn_db = _get_conn()
    try:
        with conn_db.cursor() as cur:
            cur.execute(f"SELECT `product_fournisseur_url` FROM `{table}`")
            urls: set[str] = set()
            for (url,) in cur.fetchall():
                if not url:
                    continue
                urls.add(url)
                urls.add(normaliser_url(url))
            return urls
    except pymysql.Error as e:
        _log.exception("get_scraped_product_urls(%s) échec : %s", site, e)
        return set()
    finally:
        conn_db.close()


def get_product_references(_conn, site: str) -> list[str]:
    """Retourne toutes les références fournisseur (non vides) déjà en base.

    Utilisé par le mode « MAJ prix/stock GraphQL » pour savoir quels produits
    rafraîchir.
    """
    table = _table(site, "products")
    conn_db = _get_conn()
    try:
        with conn_db.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT `product_reference_fournisseur` FROM `{table}` "
                f"WHERE `product_reference_fournisseur` <> ''"
            )
            return [r[0] for r in cur.fetchall() if r[0]]
    except pymysql.Error as e:
        _log.exception("get_product_references(%s) échec : %s", site, e)
        return []
    finally:
        conn_db.close()


def update_product_fields(_conn, site: str, ref: str, fields: dict,
                          match_col: str = "product_reference_fournisseur") -> bool:
    """UPDATE ciblé de quelques colonnes pour une ligne identifiée par ``match_col``.

    Ne touche QUE les colonnes de ``fields`` (les autres — description, images,
    etc. — sont préservées). Retourne True si au moins une ligne correspond.

    match_col : colonne d'identification (défaut = référence fournisseur). Le mode
        « léger » Sider matche par ``product_fournisseur_url`` : une réf peut être
        partagée par plusieurs fiches, ce qui collisionne avec l'index UNIQUE sur
        l'URL si l'on met à jour par réf.

    Sécurité : ``match_col`` et les colonnes de ``fields`` doivent appartenir à
    ``CSV_HEADERS``.
    """
    ref = str(ref or "").strip()
    cols = [c for c in fields if c in CSV_HEADERS]
    if not ref or not cols or match_col not in CSV_HEADERS:
        return False
    table = _table(site, "products")
    set_clause = ", ".join(f"`{c}`=%s" for c in cols)
    values = [str(fields.get(c, "") or "") for c in cols]
    conn_db = _get_conn()
    try:
        with conn_db.cursor() as cur:
            cur.execute(
                f"UPDATE `{table}` SET {set_clause} WHERE `{match_col}` = %s",
                values + [ref],
            )
            # rowcount = lignes MODIFIÉES ; 0 peut signifier « inexistant » OU
            # « existant mais déjà à jour ». On lève l'ambiguïté par un SELECT.
            matched = cur.rowcount > 0
            if not matched:
                cur.execute(
                    f"SELECT 1 FROM `{table}` WHERE `{match_col}` = %s LIMIT 1",
                    (ref,),
                )
                matched = cur.fetchone() is not None
        conn_db.commit()
        return matched
    except pymysql.Error as e:
        conn_db.rollback()
        _log.exception("update_product_fields(%s) échec : %s | %s=%s", site, e, match_col, ref)
        return False
    finally:
        conn_db.close()


# ─── Marquage « vue au dernier passage » ──────────────────────────────────────

#: Uid en attente de marquage, par site. Vidé automatiquement au seuil et en fin
#: de run. Tampon de module — même portée que la connexion, et vidage déterministe.
_VUES_EN_ATTENTE: dict[str, list[str]] = defaultdict(list)

#: Uid accumulés avant écriture. Un marquage ligne à ligne referait les 80 000
#: allers-retours que le dédoublonnage a déjà payés une fois.
SEUIL_MARQUAGE = 500


def marquer_vue(site: str, uid: str | None) -> None:
    """Note qu'une fiche a été **confirmée présente** lors du passage en cours.

    Distinguer « vue et inchangée » de « pas vue du tout » est TOUTE la base du
    suivi de cycle de vie : sans ça, une fiche que le scrape retrouve identique
    est indiscernable d'une fiche disparue.

    L'écriture est différée : les uid s'accumulent et partent par lots.
    """
    if not uid:
        return
    tampon = _VUES_EN_ATTENTE[site]
    tampon.append(uid)
    if len(tampon) >= SEUIL_MARQUAGE:
        vider_marquage(site)


def vider_marquage(site: str) -> int:
    """Écrit les marquages en attente. Retourne le nombre de fiches marquées."""
    tampon = _VUES_EN_ATTENTE.get(site)
    if not tampon:
        return 0
    uids, _VUES_EN_ATTENTE[site] = tampon, []
    table = _table(site, "products")
    conn = _get_conn()
    total = 0
    try:
        with conn.cursor() as cur:
            for lot in _lots(uids):
                marqueurs = ", ".join("%s" for _ in lot)
                cur.execute(
                    f"UPDATE `{table}` SET `{COL_VUE}` = NOW(), "
                    f"`{COL_ABSENCES}` = 0, `{COL_ETAT}` = %s "
                    f"WHERE `{COLONNE_UID}` IN ({marqueurs})",
                    [ETAT_ACTIF, *lot],
                )
                total += cur.rowcount
        conn.commit()
    except pymysql.Error as e:
        conn.rollback()
        _log.warning("Marquage %s échoué (%d uid) : %s", site, len(uids), e)
    finally:
        conn.close()
    return total


# ─── Journal des runs ─────────────────────────────────────────────────────────

def ouvrir_run(site: str, methode: str | None = None) -> int | None:
    """Ouvre un run dans le journal. Retourne son id, ou None si l'écriture échoue."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO `runs_scrape` (`fournisseur`, `methode`, `debut`) "
                "VALUES (%s, %s, NOW())",
                (site, methode or ""),
            )
            conn.commit()
            return cur.lastrowid
    except pymysql.Error as e:
        conn.rollback()
        _log.warning("Ouverture de run %s impossible : %s", site, e)
        return None
    finally:
        conn.close()


def fermer_run(run_id: int | None, *, complet: bool, enumerees: int | None = None,
               ecrites: int | None = None, note: str = "") -> None:
    """Clôt un run. ``complet`` conditionne le droit de balayer.

    ⚠️ Ne passer ``complet=True`` que si l'énumération a couvert **tout** le
    catalogue : ni ``limit``, ni filtre incrémental, ni catégorie unique, ni
    interruption. Dans le doute, ``False`` — un run non complet ne fait que
    rafraîchir des fiches, ce qui est sans danger.
    """
    if run_id is None:
        return
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE `runs_scrape` SET `fin` = NOW(), `complet` = %s, "
                "`enumerees` = %s, `ecrites` = %s, `note` = %s WHERE `id` = %s",
                (1 if complet else 0, enumerees, ecrites, note, run_id),
            )
        conn.commit()
    except pymysql.Error as e:
        conn.rollback()
        _log.warning("Clôture du run %s impossible : %s", run_id, e)
    finally:
        conn.close()


def dernier_run_complet(site: str) -> dict | None:
    """Dernier run complet du fournisseur, ou None. Sert de garde au balayage."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `id`, `debut`, `fin`, `enumerees`, `ecrites` FROM `runs_scrape` "
                "WHERE `fournisseur` = %s AND `complet` = 1 AND `fin` IS NOT NULL "
                "ORDER BY `fin` DESC LIMIT 1",
                (site,),
            )
            ligne = cur.fetchone()
            if not ligne:
                return None
            return dict(zip(("id", "debut", "fin", "enumerees", "ecrites"), ligne))
    except pymysql.Error:
        return None
    finally:
        conn.close()


# ─── Application des verdicts de cycle de vie ─────────────────────────────────

def appliquer_verdicts(site: str, verdicts: dict[str, str], *,
                       apply: bool = False) -> dict:
    """Écrit l'état de cycle de vie des fiches d'après des verdicts VÉRIFIÉS.

    ``verdicts`` : ``{url: verdict}`` produit par ``verifier_retraits.py``. Seuls
    les verdicts de retrait CONFIRMÉ agissent ; « publie », « renomme » et
    « indetermine » remettent la fiche à l'état actif ou la laissent tranquille.

    ⚠️ **Aucune suppression.** Une fiche retirée garde sa ligne : on écrit son
    état, la date, et un libellé de stock que le PIM doit faire tomber à zéro.
    C'est réversible — un article qui réapparaît repasse ``actif`` au prochain
    passage du scrape (cf. ``marquer_vue``).

    Args:
        site: fournisseur.
        verdicts: ``{url: verdict}``.
        apply: False (défaut) = simulation, aucune écriture.

    Returns:
        Rapport : retires, reactives, ignores, introuvables.
    """
    from core.dedup import normaliser_url

    retraits = {
        normaliser_url(url) for url, v in verdicts.items()
        if v in ("retire", "fin_de_vie", "redirige_ailleurs")
    }
    vivants = {
        normaliser_url(url) for url, v in verdicts.items()
        if v in ("publie", "renomme")
    }
    rapport = {"site": site, "applique": apply, "retires": 0, "reactives": 0,
               "ignores": len(verdicts) - len(retraits) - len(vivants),
               "introuvables": 0}
    if not retraits and not vivants:
        return rapport

    table = _table(site, "products")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # On cible par URL normalisée : c'est la clé que la vérification a
            # utilisée, et une fiche peut porter plusieurs lignes (variantes).
            cur.execute(
                f"SELECT `id`, `product_fournisseur_url` FROM `{table}` "
                f"WHERE `product_fournisseur_url` <> ''"
            )
            par_url: dict[str, list[int]] = defaultdict(list)
            for row_id, url in cur.fetchall():
                par_url[normaliser_url(url)].append(row_id)

            ids_retires = [i for u in retraits for i in par_url.get(u, [])]
            ids_vivants = [i for u in vivants for i in par_url.get(u, [])]
            rapport["retires"] = len(ids_retires)
            rapport["reactives"] = len(ids_vivants)
            rapport["introuvables"] = len(
                [u for u in retraits | vivants if u not in par_url]
            )

            if apply:
                for lot in _lots(ids_retires):
                    marqueurs = ", ".join("%s" for _ in lot)
                    cur.execute(
                        f"UPDATE `{table}` SET `{COL_ETAT}` = %s, `{COL_RETIRE_LE}` = NOW(), "
                        f"`{COL_VERIFIE_LE}` = NOW(), `product_stock_status` = %s "
                        f"WHERE `id` IN ({marqueurs})",
                        [ETAT_RETIRE, LIBELLE_STOCK_RETIRE, *lot],
                    )
                for lot in _lots(ids_vivants):
                    marqueurs = ", ".join("%s" for _ in lot)
                    cur.execute(
                        f"UPDATE `{table}` SET `{COL_ETAT}` = %s, `{COL_ABSENCES}` = 0, "
                        f"`{COL_RETIRE_LE}` = NULL, `{COL_VERIFIE_LE}` = NOW() "
                        f"WHERE `id` IN ({marqueurs})",
                        [ETAT_ACTIF, *lot],
                    )
                conn.commit()
                _log.info("Cycle de vie %s : %d retirée(s), %d réactivée(s)",
                          site, len(ids_retires), len(ids_vivants))
            else:
                conn.rollback()
    except pymysql.Error as e:
        conn.rollback()
        _log.exception("appliquer_verdicts(%s) échec : %s", site, e)
        raise
    finally:
        conn.close()
    return rapport


# ─── Dédoublonnage rétroactif ─────────────────────────────────────────────────

def _lots(sequence: list, taille: int = 500):
    """Découpe une liste en lots de ``taille`` éléments."""
    for debut in range(0, len(sequence), taille):
        yield sequence[debut:debut + taille]


def _colonne_existe(cur, table: str, colonne: str) -> bool:
    cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (colonne,))
    return cur.fetchone() is not None


def _grouper_par_uid(cur, table: str, site: str, criteres: tuple[str, ...],
                     ) -> tuple[dict[str, list[int]], dict[int, str]]:
    """Calcule l'uid de chaque ligne et regroupe les id par uid.

    Ne lit que les colonnes de clé (pas les descriptions) : le regroupement
    d'une table de 100 000 fiches tient en mémoire sans difficulté.

    Returns:
        (groupes {uid → [id…]}, uid actuellement stocké par id). Le second
        permet de ne réécrire que les uid qui changent — sans quoi chaque
        passe post-scrape rejouerait 100 000 UPDATE inutiles.
    """
    uid_stocke = _colonne_existe(cur, table, COLONNE_UID)
    colonne_uid = f"`{COLONNE_UID}`" if uid_stocke else "NULL"
    cur.execute(
        f"SELECT `id`, `product_fournisseur_url`, `product_reference_fournisseur`,"
        f" `product_ean`, {colonne_uid} FROM `{table}` ORDER BY `id`"
    )
    groupes: dict[str, list[int]] = defaultdict(list)
    actuels: dict[int, str] = {}
    for row_id, url, ref, ean, uid_actuel in cur.fetchall():
        actuels[row_id] = uid_actuel or ""
        uid = uid_produit(site, {
            "product_fournisseur_url": url,
            "product_reference_fournisseur": ref,
            "product_ean": ean,
        }, criteres)
        if uid:
            groupes[uid].append(row_id)
    return groupes, actuels


def _ecrire_uids(cur, table: str, paires: list[tuple[str, int]]) -> None:
    """Écrit les uid d'un lot en **une seule** requête (``CASE`` sur l'id).

    ⚠️ ``executemany`` ne regroupe que les ``INSERT … VALUES`` : sur un ``UPDATE``,
    pymysql boucle et envoie une requête PAR LIGNE. Le premier passage sur une
    table de 80 000 fiches faisait donc 80 000 allers-retours vers une base
    distante — plusieurs minutes pendant lesquelles la GUI semblait figée à la
    fin de chaque scrape. Un ``CASE`` par lot ramène cela à quelques centaines de
    requêtes.
    """
    if not paires:
        return
    cas = " ".join("WHEN %s THEN %s" for _ in paires)
    marqueurs = ", ".join("%s" for _ in paires)
    valeurs = [v for uid, row_id in paires for v in (row_id, uid)]
    valeurs += [row_id for _uid, row_id in paires]
    cur.execute(
        f"UPDATE `{table}` SET `{COLONNE_UID}` = CASE `id` {cas} END "
        f"WHERE `id` IN ({marqueurs})",
        valeurs,
    )


def _lire_lignes(cur, table: str, ids: list[int]) -> dict[int, dict]:
    """Charge les lignes complètes correspondant à ``ids``."""
    colonnes = ", ".join(f"`{h}`" for h in CSV_HEADERS)
    marqueurs = ", ".join("%s" for _ in ids)
    cur.execute(
        f"SELECT `id`, {colonnes} FROM `{table}` WHERE `id` IN ({marqueurs})", ids
    )
    return {r[0]: dict(zip(CSV_HEADERS, r[1:])) for r in cur.fetchall()}


def deduplicate_products(site: str, apply: bool = False, strict: bool = True,
                         criteres: tuple[str, ...] | None = None) -> dict:
    """Fusionne les fiches produit en double déjà présentes en base.

    Pour chaque groupe de lignes partageant le même ``product_uid`` :
      1. la ligne la plus complète (``score_completude``) est conservée ;
      2. elle est enrichie des champs non vides des autres (fusion non
         destructive : aucune donnée n'est perdue) ;
      3. les autres lignes sont supprimées ;
      4. l'uid est écrit sur le survivant, ce qui rend l'index UNIQUE posable.

    En mode strict, un groupe dont les lignes ne se ressemblent pas
    (``sont_jumelles``) n'est PAS fusionné : il est signalé comme conflit et
    seul le survivant reçoit l'uid, les autres gardent un uid NULL.

    Args:
        site: fournisseur ciblé.
        apply: False (défaut) = simulation, aucune écriture.
        strict: refuse de fusionner des lignes qui divergent sur le fond.
        criteres: forcer d'autres critères d'identité que ceux du site
            (ex. ``("ref",)`` pour un audit par référence).

    Returns:
        Rapport : lignes, groupes, fusionnees, supprimees, conflits, exemples.
    """
    table = _table(site, "products")
    criteres = criteres or criteres_du_site(site)
    rapport = {
        "site": site, "table": table, "criteres": criteres, "applique": apply,
        "lignes": 0, "groupes_doublons": 0, "lignes_supprimees": 0,
        "conflits": 0, "exemples": [],
    }

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            rapport["lignes"] = cur.fetchone()[0]

            groupes, uid_actuels = _grouper_par_uid(cur, table, site, criteres)
            doublons = {uid: ids for uid, ids in groupes.items() if len(ids) > 1}
            uniques = {uid: ids[0] for uid, ids in groupes.items() if len(ids) == 1}
            rapport["groupes_doublons"] = len(doublons)

            a_supprimer: list[int] = []
            # id → uid à écrire ; les lignes non retenues gardent un uid NULL,
            # qui n'entre jamais en conflit dans un index UNIQUE.
            uid_par_id: dict[int, str] = {row_id: uid for uid, row_id in uniques.items()}

            for uid, ids in doublons.items():
                lignes = _lire_lignes(cur, table, ids)
                ordonnees = sorted(
                    ids, key=lambda i: score_completude(lignes[i], CSV_HEADERS), reverse=True
                )
                survivant = ordonnees[0]
                perdants = ordonnees[1:]

                if strict:
                    perdants = [
                        i for i in perdants if sont_jumelles(lignes[survivant], lignes[i])
                    ]
                    refuses = len(ordonnees) - 1 - len(perdants)
                    if refuses:
                        rapport["conflits"] += refuses
                        if len(rapport["exemples"]) < 10:
                            rapport["exemples"].append({
                                "type": "conflit", "uid": uid, "ids": ordonnees,
                                "url": lignes[survivant].get("product_fournisseur_url", ""),
                            })

                uid_par_id[survivant] = uid
                a_supprimer.extend(perdants)

                if perdants:
                    fusion = dict(lignes[survivant])
                    for i in perdants:
                        fusion.update(
                            champs_modifies(fusion, lignes[i], CSV_HEADERS, _COLONNES_FIGEES)
                        )
                    maj = champs_modifies(lignes[survivant], fusion, CSV_HEADERS,
                                          _COLONNES_FIGEES)
                    if len(rapport["exemples"]) < 10:
                        rapport["exemples"].append({
                            "type": "fusion", "uid": uid, "garde": survivant,
                            "supprime": perdants, "champs_recuperes": sorted(maj),
                            "url": lignes[survivant].get("product_fournisseur_url", ""),
                        })
                    if apply and maj:
                        set_clause = ", ".join(f"`{c}`=%s" for c in maj)
                        cur.execute(
                            f"UPDATE `{table}` SET {set_clause} WHERE `id` = %s",
                            list(maj.values()) + [survivant],
                        )

            rapport["lignes_supprimees"] = len(a_supprimer)

            if apply:
                for lot in _lots(a_supprimer):
                    marqueurs = ", ".join("%s" for _ in lot)
                    cur.execute(f"DELETE FROM `{table}` WHERE `id` IN ({marqueurs})", lot)
                # On n'écrit ni l'uid des lignes supprimées, ni celui des lignes
                # déjà à jour : en régime établi cette boucle ne fait rien.
                supprimes = set(a_supprimer)
                paires = [
                    (uid, i) for i, uid in uid_par_id.items()
                    if i not in supprimes and uid_actuels.get(i) != uid
                ]
                for lot in _lots(paires):
                    _ecrire_uids(cur, table, lot)
                conn.commit()
                _log.info(
                    "deduplicate_products(%s) : %d ligne(s) supprimée(s), %d uid écrits",
                    site, len(a_supprimer), len(paires),
                )
            else:
                conn.rollback()
    except pymysql.Error as e:
        conn.rollback()
        _log.exception("deduplicate_products(%s) échec : %s", site, e)
        raise
    finally:
        conn.close()

    return rapport


def dedupliquer_apres_scrape(site: str, logger=None) -> dict | None:
    """Passe de dédoublonnage à lancer à la fin d'un scrape produits.

    Filet de sécurité : ``save_product`` empêche déjà la création de doublons,
    ce balayage rattrape ce qui a pu passer au travers (fiches insérées avant
    la mise en place de product_uid, alias d'URL découverts après coup,
    scrapes concurrents). En régime établi il ne trouve rien.

    Toujours en mode strict : les groupes ambigus sont signalés, jamais
    fusionnés. N'échoue jamais — un incident de dédoublonnage ne doit pas
    faire passer un scrape réussi pour un échec.

    Args:
        site: fournisseur qui vient d'être scrapé.
        logger: journal où écrire le bilan (défaut : celui du module).

    Returns:
        Le rapport de ``deduplicate_products``, ou None si la passe a échoué.
    """
    lg = logger or _log
    try:
        _ensure_tables(site)
        # Le tampon de marquage doit partir AVANT le balayage : une fiche vue mais
        # non encore écrite passerait sinon pour absente.
        marquees = vider_marquage(site)
        if marquees:
            lg.info("Marquage %s : %d fiche(s) confirmées présentes.", site, marquees)
        rapport = deduplicate_products(site, apply=True, strict=True)
    except Exception as exc:
        lg.warning("Dédoublonnage post-scrape (%s) ignoré : %s", site, exc)
        return None

    if rapport["lignes_supprimees"] or rapport["conflits"]:
        lg.info(
            "Dédoublonnage %s : %d doublon(s) fusionné(s), %d conflit(s) signalé(s)"
            " — %d fiches uniques",
            site, rapport["lignes_supprimees"], rapport["conflits"],
            rapport["lignes"] - rapport["lignes_supprimees"],
        )
    else:
        lg.info("Dédoublonnage %s : aucun doublon (%d fiches)", site, rapport["lignes"])
    return rapport


# ─── Séquences de groupes de déclinaisons ────────────────────────────────────

def next_decli_index(site: str) -> int:
    """Consomme et retourne la prochaine valeur de la séquence de déclinaisons."""
    prefix = SITE_PREFIX[site]
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT NEXTVAL(`seq_decli_{prefix}`)")
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def get_decli_index_for_parent(site: str, parent_ref: str) -> int | None:
    """Retourne l'index de déclinaison déjà attribué à parent_ref dans la table produits."""
    if not parent_ref:
        return None
    table = _table(site, "products")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT `product_combination_index` FROM `{table}`"
                " WHERE `product_parent_reference` = %s"
                "   AND `product_combination_index` IS NOT NULL"
                "   AND `product_combination_index` <> ''"
                " LIMIT 1",
                (parent_ref,),
            )
            row = cur.fetchone()
            if row and row[0]:
                try:
                    return int(row[0])
                except (ValueError, TypeError):
                    return None
            return None
    except pymysql.Error:
        return None
    finally:
        conn.close()


def resolve_decli_index(site: str, parent_ref: str) -> int:
    """Retourne l'index de groupe de déclinaisons pour parent_ref.

    Réutilise l'index existant en base si parent_ref est déjà connu,
    sinon consomme la séquence MariaDB (atomique, thread-safe).
    """
    existing = get_decli_index_for_parent(site, parent_ref)
    if existing is not None:
        return existing
    return next_decli_index(site)


# ─── Export CSV ───────────────────────────────────────────────────────────────

def export_table_to_csv(_conn, table: str, headers: list[str], out_path: Path,
                        since=None) -> int:
    """Exporte la table vers un CSV (séparateur ;). Compat sqlite_db.

    since : date optionnelle (datetime.date). Si fournie, filtre les lignes dont
            date_cmd est >= since. Supporte les formats DD/MM/YYYY et YYYY-MM-DD.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = ", ".join(f"`{h}`" for h in headers)

    has_date_cmd = "date_cmd" in headers

    # COALESCE : essaie DD/MM/YYYY, YYYY-MM-DD, puis DD-MM-YYYY
    # (l'ancien format P1 utilisait des tirets au lieu de slashes)
    parsed_date = (
        "COALESCE("
        "   STR_TO_DATE(`date_cmd`, '%%d/%%m/%%Y'),"
        "   STR_TO_DATE(`date_cmd`, '%%Y-%%m-%%d'),"
        "   STR_TO_DATE(`date_cmd`, '%%d-%%m-%%Y')"
        " )"
    )

    conn_db = _get_conn()
    try:
        with conn_db.cursor() as cur:
            if since is not None and has_date_cmd:
                cur.execute(
                    f"SELECT {cols} FROM `{table}`"
                    f" WHERE {parsed_date} >= %s"
                    f" ORDER BY {parsed_date} DESC",
                    (since,),
                )
            elif has_date_cmd:
                cur.execute(
                    f"SELECT {cols} FROM `{table}`"
                    f" ORDER BY {parsed_date} DESC"
                )
            else:
                cur.execute(f"SELECT {cols} FROM `{table}`")
            rows = cur.fetchall()
    except pymysql.Error as e:
        _log.exception("export_table_to_csv(%s) échec : %s", table, e)
        raise
    finally:
        conn_db.close()

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_ALL)
        writer.writerow(headers)
        writer.writerows(rows)
    return len(rows)