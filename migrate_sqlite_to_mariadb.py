"""Migration SQLite → MariaDB.

Lit les 3 fichiers .db locaux et insère toutes les lignes dans MariaDB.
Stratégie :
  - products  : INSERT IGNORE (pas de UNIQUE → doublons tolérés, cohérent avec sqlite_db)
  - orders    : INSERT IGNORE sur id_cmd
  - tracking  : REPLACE INTO sur id_cmd

Relançable à tout moment sans risque de doublons sur orders/tracking.
Pour products, une seconde passe n'insère pas les mêmes lignes (INSERT IGNORE).

Usage :
    uv run python migrate_sqlite_to_mariadb.py
    uv run python migrate_sqlite_to_mariadb.py --dry-run   # compte sans insérer
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

import mysql.connector
from mysql.connector import pooling, Error as MySQLError
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("migration")

# ─── Configuration ────────────────────────────────────────────────────────────

SQLITE_DBS = {
    "legallais": Path("legallais.db"),
    "prolians":  Path("prolians.db"),
    "setin":     Path("setin.db"),
}

SITE_PREFIX = {
    "legallais": "P1",
    "prolians":  "P3",
    "setin":     "P5",
}

BATCH_SIZE = 200  # lignes par INSERT batch

# ─── Connexion MariaDB ────────────────────────────────────────────────────────

def _mariadb_conn() -> mysql.connector.MySQLConnection:
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
        connect_timeout=10,
    )

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _count_sqlite(site: str, kind: str) -> int:
    db = SQLITE_DBS[site]
    if not db.exists():
        return 0
    conn = sqlite3.connect(str(db))
    cur = conn.execute(f'SELECT COUNT(*) FROM "{site}_{kind}"')
    n = cur.fetchone()[0]
    conn.close()
    return n


def _read_sqlite(site: str, kind: str) -> tuple[list[str], list[tuple]]:
    """Retourne (headers, rows) depuis SQLite."""
    db = SQLITE_DBS[site]
    if not db.exists():
        return [], []
    conn = sqlite3.connect(str(db))
    cur = conn.execute(f'SELECT * FROM "{site}_{kind}"')
    headers = [d[0] for d in cur.description]
    rows = cur.fetchall()
    conn.close()
    return headers, rows


def _batch_insert(
    mconn: mysql.connector.MySQLConnection,
    table: str,
    headers: list[str],
    rows: list[tuple],
    conflict: str,
    dry_run: bool,
) -> tuple[int, int]:
    """Insère par batches. Retourne (insérées, ignorées)."""
    if not rows:
        return 0, 0

    cols  = ", ".join(f"`{h}`" for h in headers)
    ph    = ", ".join("%s" for _ in headers)
    if conflict == "REPLACE":
        updates = ", ".join(f"`{h}`=VALUES(`{h}`)" for h in headers)
        sql = f"INSERT INTO `{table}` ({cols}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {updates}"
    else:
        sql = f"INSERT IGNORE INTO `{table}` ({cols}) VALUES ({ph})"

    inserted = 0
    ignored  = 0

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]

        if dry_run:
            inserted += len(batch)
            continue

        cur = mconn.cursor()
        try:
            cur.executemany(sql, batch)
            mconn.commit()
            inserted += cur.rowcount
        except MySQLError :
            mconn.rollback()
            _log.exception("Batch %d→%d échec sur %s", i, i + len(batch), table)
            raise
        finally:
            cur.close()

        # Affichage progression
        done = min(i + BATCH_SIZE, len(rows))
        pct  = done * 100 // len(rows)
        print(f"\r  {table}: {done}/{len(rows)} ({pct}%) ", end="", flush=True)

    print()  # saut de ligne après la barre de progression
    if not dry_run:
        ignored = len(rows) - inserted
    return inserted, ignored


# ─── Migration d'un site ──────────────────────────────────────────────────────

def migrate_site(site: str, mconn: mysql.connector.MySQLConnection, dry_run: bool) -> dict:
    prefix = SITE_PREFIX[site]
    report = {}

    for kind, conflict in [("products", "IGNORE"), ("orders", "IGNORE"), ("tracking", "REPLACE")]:
        sqlite_table  = f"{site}_{kind}"
        mariadb_table = f"{prefix}_{kind}"

        headers, rows = _read_sqlite(site, kind)

        if not rows:
            _log.info("  %-30s → vide, ignoré", sqlite_table)
            report[kind] = {"total": 0, "inserted": 0, "ignored": 0}
            continue

        _log.info("  %-30s → %s (%d lignes)", sqlite_table, mariadb_table, len(rows))

        inserted, ignored = _batch_insert(
            mconn, mariadb_table, headers, rows, conflict, dry_run
        )
        report[kind] = {"total": len(rows), "inserted": inserted, "ignored": ignored}

    return report

# ─── Process site ───────────────────────────────────────────────────────────

def _process_site(
    site: str,
    mconn: mysql.connector.MySQLConnection,
    dry_run: bool,
) -> tuple[int, int]:
    """Traite un site et retourne (total_rows, total_inserted)."""
    db_path = SQLITE_DBS[site]

    if not db_path.exists():
        _log.warning("[%s] Fichier %s absent — ignoré", site.upper(), db_path)
        return 0, 0

    _log.info("══ %s (%s) ══", site.upper(), SITE_PREFIX[site])
    report = migrate_site(site, mconn, dry_run)

    total_rows = 0
    total_inserted = 0

    for kind, stats in report.items():
        total_rows += stats["total"]
        total_inserted += stats["inserted"]

        if stats["total"] <= 0:
            continue

        status = (
            "DRY-RUN"
            if dry_run
            else f'{stats["inserted"]} insérées, {stats["ignored"]} ignorées'
        )

        _log.info(
            "    %-12s %4d lignes  →  %s",
            kind,
            stats["total"],
            status,
        )

    return total_rows, total_inserted


def _log_summary(
    total_rows: int,
    total_inserted: int,
    elapsed: float,
    dry_run: bool,
) -> None:
    """Affiche le résumé final de la migration."""
    _log.info("══════════════════════════════════════")
    _log.info("Total lu      : %d lignes", total_rows)

    if not dry_run:
        _log.info("Total inséré  : %d lignes", total_inserted)

    _log.info("Durée         : %.1fs", elapsed)
    _log.info("══════════════════════════════════════")

    if dry_run:
        _log.info("Relance sans --dry-run pour migrer réellement.")


# ─── Point d'entrée ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Compte les lignes sans insérer")
    args = parser.parse_args()

    if args.dry_run:
        _log.info("=== MODE DRY-RUN — aucune donnée ne sera écrite ===")

    # Connexion MariaDB
    _log.info("Connexion MariaDB...")
    try:
        mconn = _mariadb_conn()
        _log.info("OK")
    except MySQLError :
        _log.exception("Connexion impossible")
        sys.exit(1)

    total_rows     = 0
    total_inserted = 0
    start          = time.time()

    print()

    for site in ("legallais", "prolians", "setin"):
        rows, inserted = _process_site(
            site,
            mconn,
            args.dry_run,
        )
        total_rows += rows
        total_inserted += inserted

    elapsed = time.time() - start
    print()

    _log_summary(
        total_rows,
        total_inserted,
        elapsed,
        args.dry_run,
    )


if __name__ == "__main__":
    main()
