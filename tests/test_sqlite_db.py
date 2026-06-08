"""
Tests unitaires pour db/sqlite_db.py — couvre les exigences fonctionnelles :

  Req 1  — Une base SQLite par site (setin, legallais, prolians)
  Req 2  — Trois tables par site ({site}_products, _orders, _tracking)
  Req 3  — Schémas générés depuis CSV_HEADERS (pas de duplication manuelle)
  Req 4  — Table produits = CSV_HEADERS exactement
  Req 5  — Table commandes = ORDERS_CSV_HEADERS exactement
  Req 6  — Table suivi = TRACKING_CSV_HEADERS exactement
  Req 8  — Pas d'ORM (sqlite3 stdlib uniquement)
  Reprise — get_scraped_product_urls, dédup INSERT OR IGNORE / REPLACE
  Export  — export_table_to_csv : table complète, colonnes dans le bon ordre
  Régression — constantes CSV_HEADERS inchangées

Lancement : pytest tests/test_sqlite_db.py -v
"""

import csv
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

# ── ensure project root is on sys.path ─────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.config import CSV_HEADERS, ORDERS_CSV_HEADERS, TRACKING_CSV_HEADERS
from db.sqlite_db import (
    SITE_DB_PATHS,
    export_table_to_csv,
    get_scraped_product_urls,
    init_site_db,
    insert_order,
    insert_product,
    insert_tracking,
)

# stdlib 'selectors' shadows the project's selectors/ package — clear if needed
if "selectors" in sys.modules and not hasattr(sys.modules["selectors"], "setin"):
    del sys.modules["selectors"]

from scrapers.Setin_P5.products.scraper_setin_products import _normalize_stock_status  # noqa: E402


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db_paths(tmp_path, monkeypatch):
    """Redirect all SITE_DB_PATHS to temporary files so tests never touch real DBs."""
    patched = {
        "setin":    tmp_path / "setin.db",
        "legallais": tmp_path / "legallais.db",
        "prolians": tmp_path / "prolians.db",
    }
    monkeypatch.setitem(SITE_DB_PATHS, "setin",     patched["setin"])
    monkeypatch.setitem(SITE_DB_PATHS, "legallais", patched["legallais"])
    monkeypatch.setitem(SITE_DB_PATHS, "prolians",  patched["prolians"])
    return patched


@pytest.fixture
def setin_conn(tmp_db_paths):
    conn = init_site_db("setin")
    yield conn
    conn.close()


@pytest.fixture
def legallais_conn(tmp_db_paths):
    conn = init_site_db("legallais")
    yield conn
    conn.close()


@pytest.fixture
def prolians_conn(tmp_db_paths):
    conn = init_site_db("prolians")
    yield conn
    conn.close()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f"PRAGMA table_info('{table}')")
    return [row[1] for row in cur.fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def _make_product_row(ref: str = "REF-001", url: str = "https://example.com/p/1") -> dict:
    row = {h: "" for h in CSV_HEADERS}
    row["product_reference_fournisseur"] = ref
    row["product_fournisseur_url"] = url
    row["product_designation"] = "Produit test"
    row["product_fournisseur"] = "P5"
    return row


def _make_order_row(id_cmd: str = "CMD-001") -> dict:
    return {
        "id_cmd":     id_cmd,
        "ref_cmd":    "REF-001",
        "date_cmd":   "01/06/2025",
        "statut_cmd": "Livré",
        "data_pdt":   "ref1:produit1:2",
    }


def _make_tracking_row(id_cmd: str = "CMD-001") -> dict:
    return {
        "id_cmd":          id_cmd,
        "ref_cmd":         "REF-001",
        "date_cmd":        "01/06/2025",
        "statut_cmd":      "Expédié",
        "data_pdt":        "ref1:produit1:1",
        "Date_Reliquat":   "",
        "weight_exp":      "1.2 kg",
        "carrier_exp":     "Chronopost",
        "trackinglink_exp": "https://track.example.com/XYZ",
        "tracking_exp":    "XYZ123",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Req 1 — One database per site
# ═══════════════════════════════════════════════════════════════════════════════

class TestOneDatabasePerSite:

    def test_three_distinct_db_paths(self, tmp_db_paths):
        paths = list(tmp_db_paths.values())
        assert len(set(str(p) for p in paths)) == 3, "Each site must have a unique DB path"

    def test_setin_db_created(self, setin_conn, tmp_db_paths):
        assert tmp_db_paths["setin"].exists()

    def test_legallais_db_created(self, legallais_conn, tmp_db_paths):
        assert tmp_db_paths["legallais"].exists()

    def test_prolians_db_created(self, prolians_conn, tmp_db_paths):
        assert tmp_db_paths["prolians"].exists()

    def test_unknown_site_raises(self, tmp_db_paths):
        with pytest.raises(ValueError, match="Unknown site"):
            init_site_db("nonexistent_site")

    def test_sites_are_isolated(self, setin_conn, legallais_conn, tmp_db_paths):
        """Inserting into setin must not appear in legallais."""
        insert_order(setin_conn, "setin", _make_order_row("CMD-SETIN"))
        cur = legallais_conn.execute("SELECT COUNT(*) FROM legallais_orders")
        assert cur.fetchone()[0] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Req 2 — Three tables per site
# ═══════════════════════════════════════════════════════════════════════════════

class TestThreeTablesPerSite:

    @pytest.mark.parametrize("site,expected_tables", [
        ("setin",    ["setin_products",    "setin_orders",    "setin_tracking"]),
        ("legallais", ["legallais_products", "legallais_orders", "legallais_tracking"]),
        ("prolians",  ["prolians_products",  "prolians_orders",  "prolians_tracking"]),
    ])
    def test_tables_exist(self, tmp_db_paths, site, expected_tables):
        conn = init_site_db(site)
        for t in expected_tables:
            assert _table_exists(conn, t), f"Table {t!r} missing in {site}.db"
        conn.close()

    def test_exactly_three_tables(self, setin_conn):
        cur = setin_conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        )
        assert cur.fetchone()[0] == 3


# ═══════════════════════════════════════════════════════════════════════════════
# Req 3 — Schemas generated from CSV_HEADERS (not hand-coded)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaFromCSVHeaders:

    def test_products_columns_match_csv_headers(self, setin_conn):
        cols = _table_columns(setin_conn, "setin_products")
        assert cols == CSV_HEADERS, (
            "setin_products columns do not match CSV_HEADERS"
        )

    def test_orders_columns_match_orders_csv_headers(self, setin_conn):
        cols = _table_columns(setin_conn, "setin_orders")
        assert cols == ORDERS_CSV_HEADERS

    def test_tracking_columns_match_tracking_csv_headers(self, setin_conn):
        cols = _table_columns(setin_conn, "setin_tracking")
        assert cols == TRACKING_CSV_HEADERS

    def test_all_sites_have_identical_product_schema(self, tmp_db_paths):
        for site in ("setin", "legallais", "prolians"):
            conn = init_site_db(site)
            cols = _table_columns(conn, f"{site}_products")
            assert cols == CSV_HEADERS, f"{site}_products schema mismatch"
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Req 4–6 — Exact column structures
# ═══════════════════════════════════════════════════════════════════════════════

class TestExactColumnStructures:

    def test_products_exact_26_columns(self, setin_conn):
        cols = _table_columns(setin_conn, "setin_products")
        assert len(cols) == 26
        assert "product_fournisseur" in cols
        assert "product_cross_sell" in cols
        assert "product_fournisseur_url" in cols

    def test_orders_exact_5_columns(self, setin_conn):
        cols = _table_columns(setin_conn, "setin_orders")
        assert cols == ["id_cmd", "ref_cmd", "date_cmd", "statut_cmd", "data_pdt"]

    def test_tracking_exact_10_columns(self, setin_conn):
        cols = _table_columns(setin_conn, "setin_tracking")
        assert cols == [
            "id_cmd", "ref_cmd", "date_cmd", "statut_cmd", "data_pdt",
            "Date_Reliquat", "weight_exp", "carrier_exp",
            "trackinglink_exp", "tracking_exp",
        ]

    def test_no_orm_scraped_at_column(self, setin_conn):
        """scraped_at must NOT appear — the schema must come from CSV headers only."""
        for table in ("setin_products", "setin_orders", "setin_tracking"):
            cols = _table_columns(setin_conn, table)
            assert "scraped_at" not in cols, f"ORM column 'scraped_at' found in {table}"


# ═══════════════════════════════════════════════════════════════════════════════
# Data insertion
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataInsertion:

    def test_insert_product(self, setin_conn):
        insert_product(setin_conn, "setin", _make_product_row())
        cur = setin_conn.execute("SELECT COUNT(*) FROM setin_products")
        assert cur.fetchone()[0] == 1

    def test_insert_product_persists_all_columns(self, setin_conn):
        row = _make_product_row(ref="REF-42", url="https://setin.fr/p/42")
        row["product_designation"] = "Boulon M8"
        row["product_price_ht"] = "1.23"
        insert_product(setin_conn, "setin", row)
        cur = setin_conn.execute(
            'SELECT product_reference_fournisseur, product_fournisseur_url, '
            'product_designation, product_price_ht FROM setin_products'
        )
        r = cur.fetchone()
        assert r == ("REF-42", "https://setin.fr/p/42", "Boulon M8", "1.23")

    def test_insert_order(self, setin_conn):
        insert_order(setin_conn, "setin", _make_order_row())
        cur = setin_conn.execute("SELECT id_cmd FROM setin_orders")
        assert cur.fetchone()[0] == "CMD-001"

    def test_insert_tracking(self, setin_conn):
        insert_tracking(setin_conn, "setin", _make_tracking_row())
        cur = setin_conn.execute("SELECT tracking_exp FROM setin_tracking")
        assert cur.fetchone()[0] == "XYZ123"

    def test_multiple_products_same_url_allowed(self, setin_conn):
        """Combination products share a URL — no UNIQUE on products table."""
        url = "https://setin.fr/p/combo"
        insert_product(setin_conn, "setin", _make_product_row("REF-A", url))
        insert_product(setin_conn, "setin", _make_product_row("REF-B", url))
        cur = setin_conn.execute("SELECT COUNT(*) FROM setin_products")
        assert cur.fetchone()[0] == 2

    def test_missing_keys_become_empty_strings(self, setin_conn):
        insert_product(setin_conn, "setin", {})  # empty dict — all cols ""
        cur = setin_conn.execute(
            "SELECT product_fournisseur, product_price_ht FROM setin_products"
        )
        r = cur.fetchone()
        assert r == ("", "")

    def test_insert_to_legallais(self, legallais_conn):
        insert_order(legallais_conn, "legallais", _make_order_row("L-CMD-001"))
        cur = legallais_conn.execute("SELECT id_cmd FROM legallais_orders")
        assert cur.fetchone()[0] == "L-CMD-001"

    def test_insert_to_prolians(self, prolians_conn):
        insert_tracking(prolians_conn, "prolians", _make_tracking_row("P-CMD-001"))
        cur = prolians_conn.execute("SELECT id_cmd FROM prolians_tracking")
        assert cur.fetchone()[0] == "P-CMD-001"


# ═══════════════════════════════════════════════════════════════════════════════
# Resume mechanism
# ═══════════════════════════════════════════════════════════════════════════════

class TestResumeMechanism:

    def test_get_scraped_product_urls_empty_db(self, setin_conn):
        urls = get_scraped_product_urls(setin_conn, "setin")
        assert urls == set()

    def test_get_scraped_product_urls_after_inserts(self, setin_conn):
        insert_product(setin_conn, "setin", _make_product_row("R1", "https://a.com/1"))
        insert_product(setin_conn, "setin", _make_product_row("R2", "https://a.com/2"))
        insert_product(setin_conn, "setin", _make_product_row("R3", "https://a.com/1"))  # dup URL
        urls = get_scraped_product_urls(setin_conn, "setin")
        assert urls == {"https://a.com/1", "https://a.com/2"}

    def test_resume_skips_already_scraped_urls(self, setin_conn):
        """Simulates the 'seen' set seeding pattern used by scrapers."""
        insert_product(setin_conn, "setin", _make_product_row("R1", "https://a.com/1"))
        seen = get_scraped_product_urls(setin_conn, "setin")
        new_urls = ["https://a.com/1", "https://a.com/2", "https://a.com/3"]
        to_scrape = [u for u in new_urls if u not in seen]
        assert to_scrape == ["https://a.com/2", "https://a.com/3"]

    def test_order_dedup_insert_or_ignore(self, setin_conn):
        """Duplicate id_cmd must be ignored, not raise."""
        insert_order(setin_conn, "setin", _make_order_row("CMD-DUP"))
        insert_order(setin_conn, "setin", _make_order_row("CMD-DUP"))  # must not raise
        cur = setin_conn.execute("SELECT COUNT(*) FROM setin_orders WHERE id_cmd='CMD-DUP'")
        assert cur.fetchone()[0] == 1

    def test_tracking_dedup_insert_or_replace(self, setin_conn):
        """Tracking rows with same id_cmd must be replaced (latest status wins)."""
        row_v1 = {**_make_tracking_row("TRK-001"), "statut_cmd": "En transit"}
        row_v2 = {**_make_tracking_row("TRK-001"), "statut_cmd": "Livré"}
        insert_tracking(setin_conn, "setin", row_v1)
        insert_tracking(setin_conn, "setin", row_v2)
        cur = setin_conn.execute(
            "SELECT statut_cmd FROM setin_tracking WHERE id_cmd='TRK-001'"
        )
        assert cur.fetchone()[0] == "Livré"

    def test_get_scraped_urls_returns_empty_set_on_missing_table(self, tmp_db_paths):
        """If the table hasn't been created yet, return empty set, no exception."""
        conn = sqlite3.connect(str(tmp_db_paths["setin"]))
        # Don't call init_site_db — table doesn't exist
        urls = get_scraped_product_urls(conn, "setin")
        assert urls == set()
        conn.close()

    def test_resume_survives_interruption(self, setin_conn, tmp_db_paths):
        """Simulate crash mid-run: data already committed must survive a re-open."""
        for i in range(5):
            insert_product(
                setin_conn, "setin",
                _make_product_row(f"REF-{i}", f"https://example.com/{i}")
            )
        setin_conn.close()

        # Re-open — data must still be there
        reopened = init_site_db("setin")
        urls = get_scraped_product_urls(reopened, "setin")
        assert len(urls) == 5
        reopened.close()


# ═══════════════════════════════════════════════════════════════════════════════
# CSV export
# ═══════════════════════════════════════════════════════════════════════════════

class TestCSVExport:

    def test_export_products_empty_table(self, setin_conn, tmp_path):
        out = tmp_path / "export.csv"
        n = export_table_to_csv(setin_conn, "setin_products", CSV_HEADERS, out)
        assert n == 0
        assert out.exists()
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f, delimiter=";"))
        assert rows == [CSV_HEADERS]  # header-only

    def test_export_products_data_rows(self, setin_conn, tmp_path):
        insert_product(setin_conn, "setin", _make_product_row("R1", "https://a.com/1"))
        insert_product(setin_conn, "setin", _make_product_row("R2", "https://a.com/2"))
        out = tmp_path / "export.csv"
        n = export_table_to_csv(setin_conn, "setin_products", CSV_HEADERS, out)
        assert n == 2
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f, delimiter=";"))
        assert len(rows) == 3  # header + 2 data rows

    def test_export_column_order_matches_csv_headers(self, setin_conn, tmp_path):
        insert_product(setin_conn, "setin", _make_product_row())
        out = tmp_path / "export.csv"
        export_table_to_csv(setin_conn, "setin_products", CSV_HEADERS, out)
        with open(out, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            assert list(reader.fieldnames) == CSV_HEADERS

    def test_export_orders(self, setin_conn, tmp_path):
        insert_order(setin_conn, "setin", _make_order_row("CMD-1"))
        insert_order(setin_conn, "setin", _make_order_row("CMD-2"))
        out = tmp_path / "orders.csv"
        n = export_table_to_csv(setin_conn, "setin_orders", ORDERS_CSV_HEADERS, out)
        assert n == 2
        with open(out, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            rows = list(reader)
        assert {r["id_cmd"] for r in rows} == {"CMD-1", "CMD-2"}

    def test_export_tracking(self, setin_conn, tmp_path):
        insert_tracking(setin_conn, "setin", _make_tracking_row("TRK-1"))
        out = tmp_path / "tracking.csv"
        n = export_table_to_csv(setin_conn, "setin_tracking", TRACKING_CSV_HEADERS, out)
        assert n == 1
        with open(out, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = list(reader)[0]
        assert row["tracking_exp"] == "XYZ123"
        assert row["carrier_exp"]  == "Chronopost"

    def test_export_tracking_column_order(self, setin_conn, tmp_path):
        insert_tracking(setin_conn, "setin", _make_tracking_row())
        out = tmp_path / "tracking.csv"
        export_table_to_csv(setin_conn, "setin_tracking", TRACKING_CSV_HEADERS, out)
        with open(out, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            assert list(reader.fieldnames) == TRACKING_CSV_HEADERS

    def test_export_full_table_all_rows(self, setin_conn, tmp_path):
        """Export must include every stored row, not just the current session."""
        for i in range(20):
            insert_product(
                setin_conn, "setin",
                _make_product_row(f"REF-{i:03d}", f"https://a.com/{i}")
            )
        out = tmp_path / "big_export.csv"
        n = export_table_to_csv(setin_conn, "setin_products", CSV_HEADERS, out)
        assert n == 20

    def test_export_legallais(self, legallais_conn, tmp_path):
        insert_order(legallais_conn, "legallais", _make_order_row("L-001"))
        out = tmp_path / "legallais_orders.csv"
        n = export_table_to_csv(
            legallais_conn, "legallais_orders", ORDERS_CSV_HEADERS, out
        )
        assert n == 1

    def test_export_prolians(self, prolians_conn, tmp_path):
        insert_tracking(prolians_conn, "prolians", _make_tracking_row("P-001"))
        out = tmp_path / "prolians_tracking.csv"
        n = export_table_to_csv(
            prolians_conn, "prolians_tracking", TRACKING_CSV_HEADERS, out
        )
        assert n == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Req 8 — No ORM (import check)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoORM:

    def test_sqlite_db_does_not_import_peewee(self):
        import importlib
        import db.sqlite_db as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "peewee" not in src.lower(), "db/sqlite_db.py must not import peewee"

    def test_sqlite_db_does_not_import_sqlalchemy(self):
        import db.sqlite_db as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "sqlalchemy" not in src.lower()

    def test_uses_stdlib_sqlite3(self):
        import db.sqlite_db as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "import sqlite3" in src


# ═══════════════════════════════════════════════════════════════════════════════
# Regression — CSV_HEADERS constants unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegressionCSVHeaders:

    def test_csv_headers_length(self):
        assert len(CSV_HEADERS) == 26

    def test_csv_headers_first_and_last(self):
        assert CSV_HEADERS[0]  == "product_fournisseur"
        assert CSV_HEADERS[-1] == "product_cross_sell"

    def test_orders_csv_headers(self):
        assert ORDERS_CSV_HEADERS == [
            "id_cmd", "ref_cmd", "date_cmd", "statut_cmd", "data_pdt"
        ]

    def test_tracking_csv_headers(self):
        assert TRACKING_CSV_HEADERS == [
            "id_cmd", "ref_cmd", "date_cmd", "statut_cmd", "data_pdt",
            "Date_Reliquat", "weight_exp", "carrier_exp",
            "trackinglink_exp", "tracking_exp",
        ]

    def test_tracking_extends_orders(self):
        """Tracking headers must start with the 5 order fields."""
        assert TRACKING_CSV_HEADERS[:5] == ORDERS_CSV_HEADERS

    def test_idempotent_init(self, tmp_db_paths):
        """Calling init_site_db twice must not raise and must keep existing data."""
        conn1 = init_site_db("setin")
        insert_product(conn1, "setin", _make_product_row("R1"))
        conn1.close()

        conn2 = init_site_db("setin")
        cur = conn2.execute("SELECT COUNT(*) FROM setin_products")
        assert cur.fetchone()[0] == 1
        conn2.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Order date integrity (Issue 7)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrderDateValidation:

    def test_insert_order_future_date_rejected(self, setin_conn):
        tomorrow = (date.today() + timedelta(days=1)).strftime("%d/%m/%Y")
        insert_order(setin_conn, "setin", {**_make_order_row("FUTURE-1"), "date_cmd": tomorrow})
        cur = setin_conn.execute("SELECT COUNT(*) FROM setin_orders")
        assert cur.fetchone()[0] == 0

    def test_insert_order_today_accepted(self, setin_conn):
        today_str = date.today().strftime("%d/%m/%Y")
        insert_order(setin_conn, "setin", {**_make_order_row("TODAY-1"), "date_cmd": today_str})
        cur = setin_conn.execute("SELECT id_cmd FROM setin_orders")
        assert cur.fetchone()[0] == "TODAY-1"

    def test_insert_order_historical_date_accepted(self, setin_conn):
        insert_order(setin_conn, "setin", {**_make_order_row("HIST-1"), "date_cmd": "15/03/2023"})
        cur = setin_conn.execute("SELECT id_cmd FROM setin_orders")
        assert cur.fetchone()[0] == "HIST-1"

    def test_insert_order_malformed_date_rejected(self, setin_conn):
        insert_order(setin_conn, "setin", {**_make_order_row("BAD-1"), "date_cmd": "not-a-date"})
        cur = setin_conn.execute("SELECT COUNT(*) FROM setin_orders")
        assert cur.fetchone()[0] == 0

    def test_insert_order_impossible_date_rejected(self, setin_conn):
        insert_order(setin_conn, "setin", {**_make_order_row("IMP-1"), "date_cmd": "31/02/2026"})
        cur = setin_conn.execute("SELECT COUNT(*) FROM setin_orders")
        assert cur.fetchone()[0] == 0

    def test_insert_order_empty_date_accepted(self, setin_conn):
        insert_order(setin_conn, "setin", {**_make_order_row("EMPTY-DATE"), "date_cmd": ""})
        cur = setin_conn.execute("SELECT id_cmd FROM setin_orders")
        assert cur.fetchone()[0] == "EMPTY-DATE"


# ═══════════════════════════════════════════════════════════════════════════════
# Setin stock status normalization (Issue 4)
# ═══════════════════════════════════════════════════════════════════════════════

class TestStockNormalization:

    def test_stock_normalization_en_stock(self):
        assert _normalize_stock_status("En stock") == "EN STOCK"

    def test_stock_normalization_disponible(self):
        assert _normalize_stock_status("Disponible") == "EN STOCK"

    def test_stock_normalization_hors_stock(self):
        assert _normalize_stock_status("Hors stock") == "PAS EN STOCK"

    def test_stock_normalization_empty(self):
        assert _normalize_stock_status("") == "PAS EN STOCK"


# ═══════════════════════════════════════════════════════════════════════════════
# Inclusive date range boundaries (Issues 1 & 3)
# ═══════════════════════════════════════════════════════════════════════════════

def _in_range(order_day: date, date_inf: datetime, date_sup: datetime) -> bool:
    """Mirror Prolians collect_orders inclusive day comparison."""
    inf = date_inf.date()
    sup = date_sup.date()
    return inf <= order_day <= sup


class TestInclusiveDateRange:

    @pytest.mark.parametrize("d_from,d_to,expected_days", [
        ("01/06/2026", "01/06/2026", 1),
        ("01/06/2026", "02/06/2026", 2),
        ("01/06/2026", "05/06/2026", 5),
    ])
    def test_single_and_multi_day_ranges(self, d_from, d_to, expected_days):
        inf = datetime.strptime(d_from, "%d/%m/%Y")
        sup = datetime.strptime(d_to, "%d/%m/%Y").replace(hour=14, minute=32, second=7)
        matched = [
            inf + timedelta(days=i)
            for i in range((sup.date() - inf.date()).days + 1)
            if _in_range((inf + timedelta(days=i)).date(), inf, sup)
        ]
        assert len(matched) == expected_days

    def test_full_month_june_2026(self):
        inf = datetime(2026, 6, 1)
        sup = datetime(2026, 6, 30, 18, 0, 0)
        days = (sup.date() - inf.date()).days + 1
        matched = sum(
            1 for i in range(days)
            if _in_range((inf + timedelta(days=i)).date(), inf, sup)
        )
        assert matched == 30

    def test_cross_month_range(self):
        inf = datetime(2026, 5, 28)
        sup = datetime(2026, 6, 3, 9, 15, 0)
        days = (sup.date() - inf.date()).days + 1
        matched = sum(
            1 for i in range(days)
            if _in_range((inf + timedelta(days=i)).date(), inf, sup)
        )
        assert matched == 7

    def test_cross_year_range(self):
        inf = datetime(2025, 12, 30)
        sup = datetime(2026, 1, 2, 23, 59, 59)
        days = (sup.date() - inf.date()).days + 1
        matched = sum(
            1 for i in range(days)
            if _in_range((inf + timedelta(days=i)).date(), inf, sup)
        )
        assert matched == 4

    def test_tracking_window_includes_day_7(self):
        """Day exactly DAYS_BACK ago must not be excluded by time-of-day."""
        days_back = 7
        date_sup = datetime.today().replace(hour=23, minute=59, second=59, microsecond=0)
        date_inf = (datetime.today() - timedelta(days=days_back)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        boundary = (datetime.today() - timedelta(days=days_back)).date()
        assert _in_range(boundary, date_inf, date_sup)
