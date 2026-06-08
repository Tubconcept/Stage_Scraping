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

import mysql.connector
from mysql.connector import pooling, Error as MySQLError
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

# ─── Pool de connexions (singleton) ──────────────────────────────────────────

_pool: pooling.MySQLConnectionPool | None = None


def _get_pool() -> pooling.MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="scraper_pool",
            pool_size=5,           # 5 connexions max simultanées
            pool_reset_session=True,
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 3306)),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
            connect_timeout=10,
        )
        _log.info("Pool MariaDB initialisé (%s:%s/%s)",
                  os.getenv("DB_HOST"), os.getenv("DB_PORT", 3306), os.getenv("DB_NAME"))
    return _pool


def _get_conn() -> mysql.connector.MySQLConnection:
    """Retourne une connexion du pool avec retry automatique (3 tentatives)."""
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            return _get_pool().get_connection()
        except MySQLError as e:
            last_err = e
            _log.warning("Connexion MariaDB échouée (tentative %d/3) : %s", attempt, e)
            time.sleep(attempt * 2)
    raise ConnectionError(f"Impossible de se connecter à MariaDB après 3 tentatives : {last_err}")


# ─── Sentinel de compatibilité ───────────────────────────────────────────────

class _ConnSentinel:
    """Remplace sqlite3.Connection dans les scrapers existants.

    Truthy → les guards `if db_conn:` passent.
    `.close()` → no-op (le pool gère le cycle de vie des connexions).
    """
    def close(self) -> None:
        pass

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        return "<MariaDB pool sentinel>"


_SENTINEL = _ConnSentinel()


# ─── Initialisation publique (compat sqlite_db) ───────────────────────────────

def init_site_db(site: str) -> _ConnSentinel:
    """Vérifie que le site est connu et que la connexion MariaDB est joignable.

    Retourne un sentinel truthy pour que les guards `if conn:` et `conn.close()`
    des scrapers continuent de fonctionner sans modification.
    Lève ValueError si le site est inconnu, ConnectionError si la DB est injoignable.
    """
    if site not in SITE_PREFIX:
        raise ValueError(f"Unknown site: {site!r}. Choose from {list(SITE_PREFIX)}")
    conn = _get_conn()
    conn.close()
    _log.info("init_site_db(%s) OK — pool MariaDB prêt", site)
    return _SENTINEL


# ─── Helpers internes ─────────────────────────────────────────────────────────

def _table(site: str, kind: str) -> str:
    """Retourne le nom de table MariaDB, ex: 'P1_products'."""
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
    """INSERT générique avec stratégie de conflit (IGNORE ou REPLACE).

    Utilise des placeholders %s (anti SQL-injection).
    Récupère/relâche la connexion depuis le pool à chaque appel.
    """
    table  = _table(site, kind)
    cols   = ", ".join(f"`{h}`" for h in headers)
    ph     = ", ".join("%s" for _ in headers)
    values = [str(row.get(h, "") or "") for h in headers]

    if conflict == "REPLACE":
        # UPDATE en place → l'id AUTO_INCREMENT original est conservé (pas de DELETE+INSERT)
        updates = ", ".join(f"`{h}`=VALUES(`{h}`)" for h in headers)
        sql = f"INSERT INTO `{table}` ({cols}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {updates}"
    else:
        sql = f"INSERT {conflict} INTO `{table}` ({cols}) VALUES ({ph})"

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, values)
        conn.commit()
    except MySQLError as e:
        conn.rollback()
        _log.error("_insert(%s, %s) échec : %s | row=%s", site, kind, e, row.get("id_cmd") or row.get("product_fournisseur_url", "?"))
        raise
    finally:
        conn.close()


# ─── Insertion publique ───────────────────────────────────────────────────────

def insert_product(conn, site: str, row: dict) -> None:
    """Insère une ligne produit (conn ignoré — compat sqlite_db)."""
    _insert(site, "products", CSV_HEADERS, row, conflict="IGNORE")


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


def insert_order(conn, site: str, row: dict) -> None:
    """Insère une commande ; ignore les doublons et dates invalides (compat sqlite_db)."""
    date_str = str(row.get("date_cmd", "") or "")
    if not _is_valid_order_date(date_str):
        _log.warning("insert_order(%s) rejeté — date invalide : %r", site, date_str)
        return
    _insert(site, "orders", ORDERS_CSV_HEADERS, row, conflict="IGNORE")


def insert_tracking(conn, site: str, row: dict) -> None:
    """Insère ou remplace une ligne de suivi (compat sqlite_db)."""
    _insert(site, "tracking", TRACKING_CSV_HEADERS, row, conflict="REPLACE")


# ─── Reprise après interruption ───────────────────────────────────────────────

def get_scraped_product_urls(conn, site: str) -> set[str]:
    """Retourne toutes les URL produit déjà en base (compat sqlite_db)."""
    table = _table(site, "products")
    conn_db = _get_conn()
    try:
        cur = conn_db.cursor()
        cur.execute(f"SELECT `product_fournisseur_url` FROM `{table}`")
        return {r[0] for r in cur.fetchall() if r[0]}
    except MySQLError as e:
        _log.error("get_scraped_product_urls(%s) échec : %s", site, e)
        return set()
    finally:
        conn_db.close()


# ─── Export CSV ───────────────────────────────────────────────────────────────

def export_table_to_csv(conn, table: str, headers: list[str], out_path: Path) -> int:
    """Exporte toute la table vers un CSV (séparateur ;). Compat sqlite_db."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = ", ".join(f"`{h}`" for h in headers)

    conn_db = _get_conn()
    try:
        cur = conn_db.cursor()
        cur.execute(f"SELECT {cols} FROM `{table}`")
        rows = cur.fetchall()
    except MySQLError as e:
        _log.error("export_table_to_csv(%s) échec : %s", table, e)
        raise
    finally:
        conn_db.close()

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_ALL)
        writer.writerow(headers)
        writer.writerows(rows)
    return len(rows)
