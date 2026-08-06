"""
Tests unitaires — helpers extraits de gui/interface.py (correction S3776).

Fonctions testées :
  _legallais_filter_page_cmds  — filtre les commandes d'une page par plage de dates
  _legallais_collect_orders    — orchestre la pagination + collecte complète
  _save_legallais_order        — navigation vers une commande + insertion BDD
  _save_prolians_order         — parse + insertion BDD d'une commande Prolians

Les dépendances lourdes (Playwright, scrapers, auth, mariadb) sont stubbées via
sys.modules avant l'import du module gui.interface.

Lancement : pytest tests/test_interface_helpers.py -v
"""

import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ── Chemin projet ───────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ── Stubs des modules lourds (avant tout import du projet) ─────────────────────

#: Clés réellement insérées par ce module — pour les retirer après l'import.
_STUBS_POSES: list[str] = []


def _stub(dotted: str) -> None:
    """Insère un ModuleType vide dans sys.modules pour chaque préfixe du chemin."""
    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        key = ".".join(parts[:i])
        if key not in sys.modules:
            sys.modules[key] = types.ModuleType(key)
            _STUBS_POSES.append(key)


_STUBS = [
    "playwright", "playwright.sync_api",
    "dotenv",
    "auth", "auth.legallais", "auth.legallais.cookie_manager_legallais",
    "auth.prolians", "auth.prolians.cookie_manager",
    "db.mariadb_db",
    "scrapers.Legallais_P1", "scrapers.Legallais_P1.orders",
    "scrapers.Legallais_P1.orders.scraper_legallais_orders",
    "scrapers.Prolians_P3", "scrapers.Prolians_P3.orders",
    "scrapers.Prolians_P3.orders.scraper_prolians_orders",
]

for _m in _STUBS:
    _stub(_m)

# Attributs minimaux attendus par gui.interface au niveau module
_sc = sys.modules.get("gui.site_config")
if _sc is None:
    _sc = types.ModuleType("gui.site_config")
    sys.modules["gui.site_config"] = _sc
    _STUBS_POSES.append("gui.site_config")
_sc.SITES_CONFIG = {}

if "core.config" not in sys.modules:
    _cc = types.ModuleType("core.config")
    _cc.CSV_HEADERS = []
    _cc.ORDERS_CSV_HEADERS = []
    _cc.TRACKING_CSV_HEADERS = []
    sys.modules["core.config"] = _cc
    _STUBS_POSES.append("core.config")
    if "core" not in sys.modules:
        sys.modules["core"] = types.ModuleType("core")
        _STUBS_POSES.append("core")

# ── Import des helpers une fois les stubs en place ─────────────────────────────
from gui.interface import (  # noqa: E402
    _legallais_filter_page_cmds,
    _legallais_collect_orders,
    _save_legallais_order,
    _save_prolians_order,
    ScraperApp,
)

# ── Retrait des stubs : ils ne devaient vivre que le temps de l'import ─────────
# ⚠️ Sans ce nettoyage, les faux modules survivent à ce fichier et **cassent les
# autres suites** : ``scrapers.Legallais_P1`` reste un ModuleType nu, et tout
# import ultérieur de ``scrapers.Legallais_P1.products.…`` échoue sur
# « n'est pas un package ». C'est ce qui rendait ``pytest tests/`` incollectable.
# On ne retire que ce qu'on a soi-même posé : un module réellement importé
# entre-temps (par un autre fichier de test) doit rester en place.
for _cle in reversed(_STUBS_POSES):
    if isinstance(sys.modules.get(_cle), types.ModuleType) and not hasattr(
        sys.modules[_cle], "__file__"
    ):
        del sys.modules[_cle]

# ── Constante de module réutilisée dans les tests ──────────────────────────────
DATE_FORMAT = "%d/%m/%Y"

_D_FROM = datetime(2026, 6, 1)
_D_TO   = datetime(2026, 6, 10)


# ═══════════════════════════════════════════════════════════════════════════════
# _legallais_filter_page_cmds
# ═══════════════════════════════════════════════════════════════════════════════

class TestLegallaisFilterPageCmds:
    """Vérifie que les commandes sont correctement filtrées par plage de dates."""

    def _mock_scraper(self, monkeypatch, cmds: list):
        mock_mod = MagicMock()
        mock_mod.get_url_cmd.return_value = cmds
        monkeypatch.setitem(
            sys.modules,
            "scrapers.Legallais_P1.orders.scraper_legallais_orders",
            mock_mod,
        )
        return mock_mod

    def test_all_in_range_returned(self, monkeypatch):
        cmds = [
            {"date_str": "01/06/2026", "link": "/1"},
            {"date_str": "05/06/2026", "link": "/2"},
            {"date_str": "10/06/2026", "link": "/3"},
        ]
        self._mock_scraper(monkeypatch, cmds)
        result = _legallais_filter_page_cmds(MagicMock(), _D_FROM, _D_TO)
        assert len(result) == 3

    def test_out_of_range_excluded(self, monkeypatch):
        cmds = [
            {"date_str": "05/06/2026", "link": "/in"},
            {"date_str": "20/06/2026", "link": "/out_after"},
            {"date_str": "25/05/2026", "link": "/out_before"},
        ]
        self._mock_scraper(monkeypatch, cmds)
        result = _legallais_filter_page_cmds(MagicMock(), _D_FROM, _D_TO)
        assert len(result) == 1
        assert result[0]["link"] == "/in"

    def test_boundary_dates_included(self, monkeypatch):
        """Les dates exactes de début et de fin doivent être incluses (intervalle fermé)."""
        cmds = [
            {"date_str": "01/06/2026", "link": "/start"},
            {"date_str": "10/06/2026", "link": "/end"},
        ]
        self._mock_scraper(monkeypatch, cmds)
        result = _legallais_filter_page_cmds(MagicMock(), _D_FROM, _D_TO)
        links = {r["link"] for r in result}
        assert links == {"/start", "/end"}

    def test_unparseable_date_included_as_fallback(self, monkeypatch):
        """Une commande dont la date est illisible doit être conservée (fallback sûr)."""
        cmds = [{"date_str": "NOT-A-DATE", "link": "/fallback"}]
        self._mock_scraper(monkeypatch, cmds)
        result = _legallais_filter_page_cmds(MagicMock(), _D_FROM, _D_TO)
        assert len(result) == 1
        assert result[0]["link"] == "/fallback"

    def test_empty_page_returns_empty_list(self, monkeypatch):
        self._mock_scraper(monkeypatch, [])
        result = _legallais_filter_page_cmds(MagicMock(), _D_FROM, _D_TO)
        assert result == []

    def test_mixed_valid_invalid_dates(self, monkeypatch):
        """Mélange de dates valides, hors-plage et illisibles."""
        cmds = [
            {"date_str": "05/06/2026", "link": "/valid_in"},
            {"date_str": "30/07/2026", "link": "/valid_out"},
            {"date_str": "INVALID",    "link": "/invalid"},
        ]
        self._mock_scraper(monkeypatch, cmds)
        result = _legallais_filter_page_cmds(MagicMock(), _D_FROM, _D_TO)
        links = {r["link"] for r in result}
        assert links == {"/valid_in", "/invalid"}


# ═══════════════════════════════════════════════════════════════════════════════
# _legallais_collect_orders
# ═══════════════════════════════════════════════════════════════════════════════

class TestLegallaisCollectOrders:
    """Vérifie l'orchestration de la pagination et la collecte complète."""

    def _make_scraper_mod(self, check_date_side_effect, page_cmds=None):
        mod = MagicMock()
        mod.check_date.side_effect = check_date_side_effect
        mod.NEXT_PAGE_BUTTON = ".next"
        mod.log_exception = MagicMock()
        # get_url_cmd est utilisé via _legallais_filter_page_cmds
        mod.get_url_cmd.return_value = page_cmds or []
        return mod

    def test_no_pagination_needed(self, monkeypatch):
        """check_date retourne True dès le premier appel : aucun clic sur 'suivant'."""
        mod = self._make_scraper_mod(
            check_date_side_effect=[True, True],  # date_to OK, date_from OK
            page_cmds=[{"date_str": "05/06/2026", "link": "/1"}],
        )
        monkeypatch.setitem(
            sys.modules, "scrapers.Legallais_P1.orders.scraper_legallais_orders", mod
        )
        page = MagicMock()
        result = _legallais_collect_orders(page, _D_FROM, _D_TO, "2026-06-10")
        page.locator.assert_not_called()
        assert len(result) == 1

    def test_pagination_until_date_to(self, monkeypatch):
        """Le scraper doit cliquer sur 'suivant' jusqu'à ce que date_to soit visible."""
        mod = self._make_scraper_mod(
            # 1er appel check_date(date_to) → False → paginate
            # 2e appel check_date(date_to) → True  → stop
            # 3e appel check_date(date_from) → True → no more pagination
            check_date_side_effect=[False, True, True],
            page_cmds=[],
        )
        monkeypatch.setitem(
            sys.modules, "scrapers.Legallais_P1.orders.scraper_legallais_orders", mod
        )
        page = MagicMock()
        page.locator.return_value.first.inner_text.return_value = "old text"
        _legallais_collect_orders(page, _D_FROM, _D_TO, "2026-06-10")
        assert page.locator.called

    def test_pagination_error_is_logged_not_raised(self, monkeypatch):
        """Une erreur de pagination ne doit pas propager d'exception."""
        mod = self._make_scraper_mod(check_date_side_effect=Exception("timeout"))
        monkeypatch.setitem(
            sys.modules, "scrapers.Legallais_P1.orders.scraper_legallais_orders", mod
        )
        result = _legallais_collect_orders(MagicMock(), _D_FROM, _D_TO, "2026-06-10")
        mod.log_exception.assert_called()
        assert isinstance(result, list)

    def test_returns_combined_cmds_from_multiple_pages(self, monkeypatch):
        """Les commandes collectées sur plusieurs pages doivent toutes être retournées."""
        call_count = {"n": 0}

        def check_date_fn(page, target):
            call_count["n"] += 1
            # date_to: False, True → 1 pagination
            # date_from: False, True → 1 pagination + collecte
            return call_count["n"] in (2, 4)

        mod = MagicMock()
        mod.check_date.side_effect = check_date_fn
        mod.NEXT_PAGE_BUTTON = ".next"
        mod.log_exception = MagicMock()
        mod.get_url_cmd.return_value = [
            {"date_str": "05/06/2026", "link": f"/page{call_count['n']}"}
        ]
        monkeypatch.setitem(
            sys.modules, "scrapers.Legallais_P1.orders.scraper_legallais_orders", mod
        )
        page = MagicMock()
        page.locator.return_value.first.inner_text.return_value = "x"
        result = _legallais_collect_orders(page, _D_FROM, _D_TO, "2026-06-10")
        assert len(result) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# _save_legallais_order
# ═══════════════════════════════════════════════════════════════════════════════

class TestSaveLegallaisOrder:
    """Vérifie que la navigation, le parsing et l'insertion BDD se déroulent correctement."""

    def _setup(self, monkeypatch, get_info_return=None, db_raises=False):
        scraper_mod = MagicMock()
        scraper_mod.BASE_URL = "https://legallais.com"
        scraper_mod.get_info.return_value = get_info_return or {
            "ref_p1":   "CMD-001",
            "ref_cmd":  "REF-001",
            "date_cmd": "05/06/2026",
            "statut":   "Livré",
            "prdt_data": "p1:produit:2",
        }
        scraper_mod.log_exception = MagicMock()
        monkeypatch.setitem(
            sys.modules, "scrapers.Legallais_P1.orders.scraper_legallais_orders", scraper_mod
        )

        db_mod = MagicMock()
        if db_raises:
            db_mod.insert_order.side_effect = Exception("DB error")
        monkeypatch.setitem(sys.modules, "db.mariadb_db", db_mod)

        return scraper_mod, db_mod

    def test_page_navigation_called(self, monkeypatch):
        scraper_mod, _ = self._setup(monkeypatch)
        page = MagicMock()
        _save_legallais_order(page, {"link": "/cmd/1"}, MagicMock(), "2026-06-10")
        page.goto.assert_called_once_with("https://legallais.com/cmd/1")
        page.wait_for_load_state.assert_called_once_with("domcontentloaded")

    def test_db_insert_called_with_correct_row(self, monkeypatch):
        scraper_mod, db_mod = self._setup(monkeypatch)
        _save_legallais_order(MagicMock(), {"link": "/cmd/1"}, MagicMock(), "2026-06-10")
        db_mod.insert_order.assert_called_once()
        _, site, row = db_mod.insert_order.call_args[0]
        assert site == "legallais"
        assert row["id_cmd"]     == "CMD-001"
        assert row["statut_cmd"] == "Livré"

    def test_db_error_is_swallowed(self, monkeypatch):
        """Une erreur d'insertion en BDD ne doit pas planter le scraper."""
        self._setup(monkeypatch, db_raises=True)
        _save_legallais_order(MagicMock(), {"link": "/cmd/1"}, MagicMock(), "2026-06-10")

    def test_page_error_logged_not_raised(self, monkeypatch):
        """Une erreur de navigation doit être loguée, pas propagée."""
        scraper_mod, _ = self._setup(monkeypatch)
        page = MagicMock()
        page.goto.side_effect = Exception("nav error")
        _save_legallais_order(page, {"link": "/cmd/1"}, MagicMock(), "2026-06-10")
        scraper_mod.log_exception.assert_called_once()

    def test_missing_link_key_handled(self, monkeypatch):
        """Une commande sans clé 'link' ne doit pas planter (log_exception appelé)."""
        scraper_mod, _ = self._setup(monkeypatch)
        page = MagicMock()
        page.goto.side_effect = KeyError("link")
        _save_legallais_order(page, {}, MagicMock(), "2026-06-10")
        scraper_mod.log_exception.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# _save_prolians_order
# ═══════════════════════════════════════════════════════════════════════════════

class TestSaveProlianOrder:
    """Vérifie le parsing et l'insertion d'une commande Prolians."""

    def _setup(self, monkeypatch, get_info_return=None, data_none=False, db_raises=False):
        scraper_mod = MagicMock()
        scraper_mod.get_info.return_value = None if data_none else (get_info_return or {
            "webref":    "WR-001",
            "ref_cmd":   "REF-001",
            "date_cmd":  "05/06/2026",
            "statut_cmd": "Livré",
            "prdt_data": "p1:produit:1",
        })
        scraper_mod.log_exception = MagicMock()
        monkeypatch.setitem(
            sys.modules,
            "scrapers.Prolians_P3.orders.scraper_prolians_orders",
            scraper_mod,
        )

        db_mod = MagicMock()
        if db_raises:
            db_mod.insert_order.side_effect = Exception("DB error")
        monkeypatch.setitem(sys.modules, "db.mariadb_db", db_mod)

        return scraper_mod, db_mod

    def test_db_insert_called_with_correct_row(self, monkeypatch):
        _, db_mod = self._setup(monkeypatch)
        _save_prolians_order(MagicMock(), {"webref": "WR-001"}, MagicMock(), "2026-06-10")
        db_mod.insert_order.assert_called_once()
        _, site, row = db_mod.insert_order.call_args[0]
        assert site == "prolians"
        assert row["id_cmd"] == "WR-001"

    def test_no_data_skips_insert(self, monkeypatch):
        """Si get_info retourne None, aucune insertion ne doit avoir lieu."""
        _, db_mod = self._setup(monkeypatch, data_none=True)
        _save_prolians_order(MagicMock(), {"webref": "WR-001"}, MagicMock(), "2026-06-10")
        db_mod.insert_order.assert_not_called()

    def test_db_error_is_printed_not_raised(self, monkeypatch, capsys):
        """Une erreur d'insertion affiche un message mais ne propage pas d'exception."""
        self._setup(monkeypatch, db_raises=True)
        _save_prolians_order(MagicMock(), {"webref": "WR-001"}, MagicMock(), "2026-06-10")
        captured = capsys.readouterr()
        assert "[DB ERROR]" in captured.out

    def test_get_info_error_logged_not_raised(self, monkeypatch):
        """Une erreur de scraping doit être loguée, pas propagée."""
        scraper_mod, _ = self._setup(monkeypatch)
        scraper_mod.get_info.side_effect = Exception("scrape error")
        _save_prolians_order(MagicMock(), {"webref": "WR-001"}, MagicMock(), "2026-06-10")
        scraper_mod.log_exception.assert_called_once()

    def test_row_mapping_uses_correct_keys(self, monkeypatch):
        """Les clés du dict inséré doivent correspondre exactement au schéma orders."""
        _, db_mod = self._setup(monkeypatch, get_info_return={
            "webref":    "WR-042",
            "ref_cmd":   "R-042",
            "date_cmd":  "10/06/2026",
            "statut_cmd": "En cours",
            "prdt_data": "pdt:desc:3",
        })
        _save_prolians_order(MagicMock(), {}, MagicMock(), "2026-06-10")
        _, _, row = db_mod.insert_order.call_args[0]
        assert set(row.keys()) == {"id_cmd", "ref_cmd", "date_cmd", "statut_cmd", "data_pdt"}
        assert row["id_cmd"]      == "WR-042"
        assert row["statut_cmd"]  == "En cours"
        assert row["data_pdt"]    == "pdt:desc:3"


# ═══════════════════════════════════════════════════════════════════════════════
# ScraperApp._validate_days_filter
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateDaysFilter:
    """Vérifie la validation du filtre 'nombre de jours' pour l'export suivi."""

    def _make_app(self, limit_enabled: bool = False, days_value: str = "7"):
        app = MagicMock()
        panel = MagicMock()
        panel.limit_days_var.get.return_value = limit_enabled
        panel.days_var.get.return_value = days_value
        app._panels = {"suivi": panel}
        return app

    def test_no_filter_returns_none_and_ok(self):
        app = self._make_app(limit_enabled=False)
        since, nb_jours, ok = ScraperApp._validate_days_filter(app)
        assert ok is True
        assert since is None
        assert nb_jours is None

    def test_valid_7_days_returns_correct_since(self):
        app = self._make_app(limit_enabled=True, days_value="7")
        since, nb_jours, ok = ScraperApp._validate_days_filter(app)
        assert ok is True
        assert nb_jours == 7
        assert since == date.today() - timedelta(days=6)

    def test_boundary_1_day_is_valid(self):
        app = self._make_app(limit_enabled=True, days_value="1")
        since, nb_jours, ok = ScraperApp._validate_days_filter(app)
        assert ok is True
        assert nb_jours == 1
        assert since == date.today()

    def test_boundary_30_days_is_valid(self):
        app = self._make_app(limit_enabled=True, days_value="30")
        since, nb_jours, ok = ScraperApp._validate_days_filter(app)
        assert ok is True
        assert nb_jours == 30
        assert since == date.today() - timedelta(days=29)

    def test_invalid_string_shows_error_and_returns_false(self):
        app = self._make_app(limit_enabled=True, days_value="abc")
        with patch("gui.interface.messagebox") as mock_mb:
            since, nb_jours, ok = ScraperApp._validate_days_filter(app)
        assert ok is False
        assert since is None
        mock_mb.showerror.assert_called_once()

    def test_out_of_range_31_shows_error_and_returns_false(self):
        app = self._make_app(limit_enabled=True, days_value="31")
        with patch("gui.interface.messagebox") as mock_mb:
            since, nb_jours, ok = ScraperApp._validate_days_filter(app)
        assert ok is False
        mock_mb.showerror.assert_called_once()

    def test_tck_error_treated_as_invalid(self):
        """TclError sur days_var.get() → nb_jours=0 → hors plage → erreur affichée."""
        from tkinter import TclError
        app = self._make_app(limit_enabled=True)
        app._panels["suivi"].days_var.get.side_effect = TclError("invalid")
        with patch("gui.interface.messagebox") as mock_mb:
            since, nb_jours, ok = ScraperApp._validate_days_filter(app)
        assert ok is False
        mock_mb.showerror.assert_called_once()
