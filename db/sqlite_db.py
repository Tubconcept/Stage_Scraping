"""Persistance SQLite — sans ORM, sans couche d'abstraction.

Chaque fournisseur possède son propre fichier .db à la racine du projet.
Les schémas de tables sont générés automatiquement à partir des constantes
CSV_HEADERS dans core.config (source unique de vérité).

Tables par site « {site}_products », « {site}_orders », « {site}_tracking ».

API publique
------------
init_site_db(site)                        → sqlite3.Connection
insert_product(conn, site, row)
insert_order(conn, site, row)
insert_tracking(conn, site, row)
get_scraped_product_urls(conn, site)      → set[str]   # reprise après crash
export_table_to_csv(conn, table, headers, out_path) → int (nombre de lignes)
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from datetime import date as _date, datetime as _datetime
from pathlib import Path

from core.config import (
    CSV_HEADERS,
    DIRECTORY,
    ORDERS_CSV_HEADERS,
    TRACKING_CSV_HEADERS,
)

_log = logging.getLogger(__name__)

# ─── Chemins des bases — une par fournisseur ─────────────────────────────────

SITE_DB_PATHS: dict[str, Path] = {
    "setin":     DIRECTORY / "setin.db",
    "legallais": DIRECTORY / "legallais.db",
    "prolians":  DIRECTORY / "prolians.db",
}


# ─── Helpers internes ────────────────────────────────────────────────────────

def _open_connection(path: Path) -> sqlite3.Connection:
    """Ouvre (ou crée) une connexion SQLite avec mode WAL pour de meilleures perfs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")   # lectures concurrentes possibles
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_table(
    conn: sqlite3.Connection,
    table: str,
    headers: list[str],
    unique_col: str | None = None,
) -> None:
    """Crée la table si absente ; toutes les colonnes sont en TEXT."""
    col_defs = ",\n    ".join(f'"{h}" TEXT' for h in headers)
    unique_clause = f',\n    UNIQUE("{unique_col}")' if unique_col else ""
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS "{table}" (\n    {col_defs}{unique_clause}\n)'
    )
    conn.commit()


# ─── Initialisation publique ─────────────────────────────────────────────────

def init_site_db(site: str) -> sqlite3.Connection:
    """Ouvre la base du site et crée les 3 tables si nécessaire.

    Args:
        site: « setin », « legallais » ou « prolians ».

    Returns:
        Connexion SQLite prête à l'emploi.

    Raises:
        ValueError: si le nom de site est inconnu.
    """
    path = SITE_DB_PATHS.get(site)
    if path is None:
        raise ValueError(f"Unknown site: {site!r}.  Choose from {list(SITE_DB_PATHS)}")

    conn = _open_connection(path)

    # Produits : pas de UNIQUE — une URL peut avoir plusieurs lignes (déclinaisons).
    # La déduplication se fait via le set « seen » chargé au démarrage du scraper.
    _ensure_table(conn, f"{site}_products", CSV_HEADERS)

    # Commandes : id_cmd unique → INSERT OR IGNORE sur doublon
    _ensure_table(conn, f"{site}_orders", ORDERS_CSV_HEADERS, unique_col="id_cmd")

    # Suivi : même id_cmd peut être mis à jour → INSERT OR REPLACE
    _ensure_table(conn, f"{site}_tracking", TRACKING_CSV_HEADERS, unique_col="id_cmd")
    return conn


# ─── Insertion ───────────────────────────────────────────────────────────────

def _insert(
    conn: sqlite3.Connection,
    table: str,
    headers: list[str],
    row: dict,
    conflict: str = "FAIL",
) -> None:
    """INSERT générique avec stratégie de conflit (FAIL, IGNORE, REPLACE)."""
    cols   = ", ".join(f'"{h}"' for h in headers)
    ph     = ", ".join("?" for _ in headers)
    values = [str(row.get(h, "") or "") for h in headers]
    conn.execute(
        f'INSERT OR {conflict} INTO "{table}" ({cols}) VALUES ({ph})', values
    )
    conn.commit()


def insert_product(conn: sqlite3.Connection, site: str, row: dict) -> None:
    """Insère une ligne produit ; échoue si contrainte violée (rare)."""
    _insert(conn, f"{site}_products", CSV_HEADERS, row, conflict="FAIL")


def _is_valid_order_date(date_str: str) -> bool:
    """Valide la date de commande avant insertion.

    Accepte : chaîne vide, date passée ou aujourd'hui.
    Rejette : dates futures, formats illisibles, dates calendaires impossibles.
    """
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


def insert_order(conn: sqlite3.Connection, site: str, row: dict) -> None:
    """Insère une commande ; ignore les doublons (même id_cmd) et dates invalides."""
    date_str = str(row.get("date_cmd", "") or "")
    if not _is_valid_order_date(date_str):
        _log.warning("insert_order(%s) rejeté — date invalide ou future : %r", site, date_str)
        return
    _insert(conn, f"{site}_orders", ORDERS_CSV_HEADERS, row, conflict="IGNORE")


def insert_tracking(conn: sqlite3.Connection, site: str, row: dict) -> None:
    """Insère ou remplace une ligne de suivi (dernier statut conservé)."""
    _insert(conn, f"{site}_tracking", TRACKING_CSV_HEADERS, row, conflict="REPLACE")


# ─── Reprise après interruption ──────────────────────────────────────────────

def get_scraped_product_urls(conn: sqlite3.Connection, site: str) -> set[str]:
    """Retourne toutes les URL produit déjà en base pour ce site.

    Utilisé au démarrage des scrapers produits pour remplir le set « seen »
    et éviter de re-scraper les fiches déjà collectées (reprise crash-safe).
    """
    table = f"{site}_products"
    try:
        cur = conn.execute(f'SELECT "product_fournisseur_url" FROM "{table}"')
        return {r[0] for r in cur.fetchall() if r[0]}
    except sqlite3.OperationalError:
        return set()


# ─── Export CSV (table complète) ─────────────────────────────────────────────

def export_table_to_csv(
    conn: sqlite3.Connection,
    table: str,
    headers: list[str],
    out_path: Path,
) -> int:
    """Exporte toute la table vers un CSV (séparateur ;, guillemets sur chaque champ).

    Returns:
        Nombre de lignes de données écrites (hors en-tête).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = ", ".join(f'"{h}"' for h in headers)
    cur  = conn.execute(f'SELECT {cols} FROM "{table}"')
    rows = cur.fetchall()
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_ALL)
        writer.writerow(headers)
        writer.writerows(rows)
    return len(rows)
