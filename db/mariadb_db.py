"""Persistance MariaDB — API identique à db/sqlite_db.py.

Remplace sqlite_db dans les scrapers : changer l'import suffit.

Tables cibles dans Scraper_base :
  {PREFIX}_products  /  {PREFIX}_orders  /  {PREFIX}_tracking
  où PREFIX = P1 (legallais), P3 (prolians), P5 (setin)

API publique
------------
init_site_db(site)                        → connexion (inutilisée, kept for compat)
insert_product(conn, site, row)
insert_order(conn, site, row)
insert_tracking(conn, site, row)
get_scraped_product_urls(conn, site)      → set[str]
export_table_to_csv(conn, table, headers, out_path) → int
"""

from __future__ import annotations

import csv
import logging
import os
import time
from datetime import date as _date, datetime as _datetime
from pathlib import Path

import pymysql
from dotenv import load_dotenv

from core.config import CSV_HEADERS, ORDERS_CSV_HEADERS, TRACKING_CSV_HEADERS

load_dotenv()

_log = logging.getLogger(__name__)

# ─── Mapping site → préfixe de table ─────────────────────────────────────────

SITE_PREFIX: dict[str, str] = {
    "legallais": "P1",
    "prolians":  "P3",
    "setin":     "P5",
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


# ─── Création des tables ──────────────────────────────────────────────────────

def _ensure_tables(site: str) -> None:
    """Crée les 3 tables et la séquence de déclinaisons du site si nécessaire."""
    prefix = SITE_PREFIX[site]
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            for kind, headers, unique_col in [
                ("products", CSV_HEADERS, None),
                ("orders",   ORDERS_CSV_HEADERS,   "id_cmd"),
                ("tracking", TRACKING_CSV_HEADERS, "id_cmd"),
            ]:
                table = f"{prefix}_{kind}"
                col_defs = ",\n    ".join(f"`{h}` TEXT" for h in headers)
                unique_clause = f",\n    UNIQUE KEY `uq_{table}` (`{unique_col}`(255))" if unique_col else ""
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS `{table}` (\n"
                    f"    `id` INT AUTO_INCREMENT PRIMARY KEY,\n"
                    f"    {col_defs}{unique_clause}\n"
                    f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
                )
                _log.debug("Table prête : %s", table)
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


# ─── Insertion publique ───────────────────────────────────────────────────────

def insert_product(_conn, site: str, row: dict) -> None:
    """Insère une ligne produit (conn ignoré — compat sqlite_db)."""
    _insert(site, "products", CSV_HEADERS, row, conflict="IGNORE")


def upsert_product(_conn, site: str, row: dict) -> None:
    """INSERT si la référence est inconnue, UPDATE si elle existe déjà.

    Critère d'identification (priorité décroissante) :
      1. product_reference_fournisseur  — référence catalogue du fournisseur
      2. product_fournisseur_url        — URL de la fiche produit (fallback)

    Utilisé par les scrapers « mise à jour par références » pour éviter
    les doublons sans modifier le schéma des tables.
    """
    ref = str(row.get("product_reference_fournisseur", "") or "").strip()
    url = str(row.get("product_fournisseur_url", "") or "").strip()

    if ref:
        where_col, where_val = "product_reference_fournisseur", ref
    elif url:
        where_col, where_val = "product_fournisseur_url", url
    else:
        # Aucun identifiant → INSERT classique
        _insert(site, "products", CSV_HEADERS, row, conflict="IGNORE")
        return

    table = _table(site, "products")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT `id` FROM `{table}` WHERE `{where_col}` = %s LIMIT 1",
                (where_val,),
            )
            existing = cur.fetchone()

            if existing:
                row_id = existing[0]
                set_clause = ", ".join(f"`{h}`=%s" for h in CSV_HEADERS)
                values = [str(row.get(h, "") or "") for h in CSV_HEADERS]
                cur.execute(
                    f"UPDATE `{table}` SET {set_clause} WHERE `id` = %s",
                    values + [row_id],
                )
                _log.debug("upsert_product(%s) UPDATE id=%s ref=%s", site, row_id, where_val)
            else:
                cols = ", ".join(f"`{h}`" for h in CSV_HEADERS)
                ph   = ", ".join("%s" for _ in CSV_HEADERS)
                values = [str(row.get(h, "") or "") for h in CSV_HEADERS]
                cur.execute(f"INSERT INTO `{table}` ({cols}) VALUES ({ph})", values)
                _log.debug("upsert_product(%s) INSERT ref=%s", site, where_val)

        conn.commit()
    except pymysql.Error as e:
        conn.rollback()
        _log.exception("upsert_product(%s) échec : %s | ref=%s", site, e, where_val)
        raise
    finally:
        conn.close()


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
    """Retourne toutes les URL produit déjà en base (compat sqlite_db)."""
    table = _table(site, "products")
    conn_db = _get_conn()
    try:
        with conn_db.cursor() as cur:
            cur.execute(f"SELECT `product_fournisseur_url` FROM `{table}`")
            return {r[0] for r in cur.fetchall() if r[0]}
    except pymysql.Error as e:
        _log.exception("get_scraped_product_urls(%s) échec : %s", site, e)
        return set()
    finally:
        conn_db.close()


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