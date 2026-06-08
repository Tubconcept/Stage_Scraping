"""
Interface graphique Tkinter — point de contrôle unique des scrapers.

Architecture :
  - L'utilisateur choisit un site (Setin / Legallais / Prolians) puis une action.
  - Chaque action affiche un panneau avec champs optionnels (catégorie, dates).
  - Le scraping s'exécute dans un thread séparé pour ne pas bloquer l'UI.
  - Les données sont persistées en SQLite ; le bouton « Télécharger CSV » exporte la table.

Modes d'exécution par site :
  - Setin     : scrapers async Playwright (BaseScraper) via _start_async
  - Prolians  : produits/suivi async ; commandes/suppression en sync Playwright
  - Legallais : produits Botasaurus sync ; commandes/suivi/suppression Playwright sync

Wrappers _run_*_sync : contournent les input() des scripts CLI pour être appelables depuis la GUI.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from importlib import import_module

from gui.site_config import SITES_CONFIG
from core.config import CSV_HEADERS, ORDERS_CSV_HEADERS, TRACKING_CSV_HEADERS

# ─── Couleurs ──────────────────────────────────────────────────────────────────
BG        = "#FFFDE7"
YELLOW    = "#F9A825"
YELLOW_ON = "#E65100"
BTN_GRN   = "#388E3C"
BTN_RED   = "#C62828"
BTN_BLU   = "#1565C0"
BLACK     = "#1A1A1A"
WHITE     = "#FFFFFF"
GREEN_TXT = "#2E7D32"
GRAY      = "#757575"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_DIR      = PROJECT_ROOT / "csv"


# ─── Wrapper Legallais products (Botasaurus sync) ────────────────────────────

def _run_legallais_products_sync(category_filter: str | None) -> str:
    from dotenv import load_dotenv
    from core.config import CSV_DIR as _CSV_DIR
    load_dotenv(PROJECT_ROOT / ".env")

    run_ts       = datetime.today().strftime("%Y-%m-%d_%H-%M")
    csv_filename = f"scrap_p1_products_{category_filter or 'all'}_{run_ts}.csv"

    from scrapers.Legallais_P1.products.scrap_legallais_products import _scrape_direct
    _scrape_direct({
        "products":        [],
        "csv_filename":    csv_filename,
        "mode":            "browse",
        "category_filter": category_filter or None,
    })

    path = _CSV_DIR / csv_filename
    return str(path) if path.exists() else ""


# ─── Wrapper Legallais orders (Playwright sync, évite input()) ────────────────

def _run_legallais_orders_sync(date_from: datetime, date_to: datetime) -> str:
    from dotenv import load_dotenv
    from playwright.sync_api import sync_playwright
    from auth.legallais.cookie_manager_legallais import ensure_logged_in, get_session_state
    from db.sqlite_db import init_site_db, insert_order as _db_insert_order
    from scrapers.Legallais_P1.orders.scraper_legallais_orders import (
        BASE_URL, NEXT_PAGE_BUTTON, log_exception, get_url_cmd, check_date, get_Info,
    )

    load_dotenv(PROJECT_ROOT / ".env")
    user      = os.getenv("User_P1", "")
    password  = os.getenv("Password_P1", "")
    today_str = datetime.today().strftime("%Y-%m-%d")

    db_conn = init_site_db("legallais")

    with sync_playwright() as pw:
        browser  = pw.chromium.launch(headless=False)
        storage  = get_session_state()
        context  = browser.new_context(storage_state=storage)
        page     = context.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})

        if not ensure_logged_in(page, context, user, password):
            browser.close()
            db_conn.close()
            return ""

        page.goto(BASE_URL + "/user/order")
        page.wait_for_load_state("domcontentloaded")

        Url_cmd = []
        try:
            while not check_date(page, date_to):
                old_text = page.locator("tbody tr").first.inner_text()
                page.locator(NEXT_PAGE_BUTTON).click()
                page.wait_for_function(
                    "(old) => { const r = document.querySelector('tbody tr'); return r && r.innerText !== old; }",
                    arg=old_text,
                )
        except Exception as e:
            log_exception(today_str, e, "pagination date fin")

        for cmd in get_url_cmd(page):
            try:
                row_date = datetime.strptime(cmd["date_str"], "%d/%m/%Y")
            except Exception:
                Url_cmd.append(cmd)
                continue
            if date_from <= row_date <= date_to:
                Url_cmd.append(cmd)

        try:
            while not check_date(page, date_from):
                old_text = page.locator("tbody tr").first.inner_text()
                page.locator(NEXT_PAGE_BUTTON).click()
                page.wait_for_function(
                    "(old) => { const r = document.querySelector('tbody tr'); return r && r.innerText !== old; }",
                    arg=old_text,
                )
                for cmd in get_url_cmd(page):
                    try:
                        row_date = datetime.strptime(cmd["date_str"], "%d/%m/%Y")
                    except Exception:
                        Url_cmd.append(cmd)
                        continue
                    if date_from <= row_date <= date_to:
                        Url_cmd.append(cmd)
        except Exception as e:
            log_exception(today_str, e, "pagination date début")

        for cmd in Url_cmd:
            try:
                page.goto(BASE_URL + cmd["link"])
                page.wait_for_load_state("domcontentloaded")
                data = get_Info(page, cmd)
                row = {
                    "id_cmd":     data.get("ref_p1", ""),
                    "ref_cmd":    data.get("ref_cmd", ""),
                    "date_cmd":   data.get("date_cmd", ""),
                    "statut_cmd": data.get("statut", ""),
                    "data_pdt":   data.get("prdt_data", ""),
                }
                try:
                    _db_insert_order(db_conn, "legallais", row)
                except Exception:
                    pass
            except Exception as e:
                log_exception(today_str, e, f"commande {cmd.get('link', '?')}")

        browser.close()
    db_conn.close()
    return ""


# ─── Wrapper Prolians orders (évite main() qui utilise input()) ───────────────

def _run_prolians_orders_sync(date_from: datetime, date_to: datetime) -> str:
    from dotenv import load_dotenv
    from playwright.sync_api import sync_playwright
    from auth.prolians.cookie_manager import ensure_logged_in
    from db.sqlite_db import init_site_db, insert_order as _db_insert_order
    from scrapers.Prolians_P3.orders.scraper_prolians_orders import (
        navigate_to_orders, collect_orders, get_info, log_exception,
    )

    load_dotenv(PROJECT_ROOT / ".env")
    user      = os.getenv("User_P3", "")
    password  = os.getenv("Password_P3", "")
    today_str = datetime.today().strftime("%Y-%m-%d")

    db_conn = init_site_db("prolians")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx     = browser.new_context()
        ctx.set_default_timeout(10000)
        ctx.set_default_navigation_timeout(15000)
        page = ctx.new_page()

        if not ensure_logged_in(page, ctx, user, password):
            browser.close()
            db_conn.close()
            return ""

        navigate_to_orders(page)
        orders = collect_orders(page, date_from, date_to)

        for order in orders:
            try:
                data = get_info(page, order)
                if data:
                    row = {
                        "id_cmd":     data.get("webref", ""),
                        "ref_cmd":    data.get("ref_cmd", ""),
                        "date_cmd":   data.get("date_cmd", ""),
                        "statut_cmd": data.get("statut_cmd", ""),
                        "data_pdt":   data.get("prdt_data", ""),
                    }
                    try:
                        _db_insert_order(db_conn, "prolians", row)
                    except Exception:
                        pass
            except Exception as exc:
                log_exception(today_str, exc, f"Commande {order.get('webref', '?')}")

        browser.close()
    db_conn.close()
    return ""


def _parse_date(s: str) -> datetime:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Format invalide : {s!r}  —  attendu JJ/MM/AAAA")


# ─── Application ──────────────────────────────────────────────────────────────

class ScraperApp(tk.Tk):
    """Fenêtre principale : sélection site/action, lancement et arrêt des scrapers."""

    def __init__(self):
        super().__init__()
        self.title("Scraper Interface")
        self.configure(bg=BG)
        self.geometry("760x640")
        self.resizable(True, True)

        self._site:    str | None = None
        self._action:  str | None = None
        self._scraper             = None
        self._running: bool       = False
        self._csv_by_action: dict[str, str] = {}
        self._worker_thread: threading.Thread | None = None
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._async_task = None

        self._build_header()
        self._build_body()

    # ─── Construction ─────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = tk.Frame(self, bg=YELLOW, pady=14)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Scraper Interface",
                 font=("Helvetica", 18, "bold"), bg=YELLOW, fg=BLACK).pack()

    def _build_body(self):
        bg = tk.Frame(self, bg=BG)
        bg.pack(fill="both", expand=True)

        # Contenu centré — relx=0.5 maintient le centrage lors du redimensionnement
        self._content = tk.Frame(bg, bg=BG)
        self._content.place(relx=0.5, rely=0.0, anchor="n")

        # Spacer de largeur minimale pour forcer le centrage à 700 px
        tk.Frame(self._content, bg=BG, width=700, height=1).pack()

        self._build_site_section()
        self._build_action_section()
        self._build_panels()

    def _build_site_section(self):
        frm = tk.Frame(self._content, bg=BG, pady=16)
        frm.pack(fill="x")

        tk.Label(frm, text="Choisir le site :",
                 font=("Helvetica", 12, "bold"), bg=BG, fg=BLACK).pack()

        row = tk.Frame(frm, bg=BG)
        row.pack(pady=7)
        self._site_btns: dict[str, tk.Button] = {}
        for name in SITES_CONFIG:
            b = tk.Button(row, text=name, font=("Helvetica", 11, "bold"),
                          bg=YELLOW, fg=BLACK, padx=26, pady=10,
                          relief="flat", cursor="hand2",
                          command=lambda n=name: self._select_site(n))
            b.pack(side="left", padx=8)
            self._site_btns[name] = b

        self._lbl_site = tk.Label(frm, text="Aucun site sélectionné",
                                   font=("Helvetica", 9, "italic"), bg=BG, fg=GRAY)
        self._lbl_site.pack()

    def _build_action_section(self):
        self._action_frm = tk.Frame(self._content, bg=BG, pady=8)
        # pack() déclenché dans _select_site

        ttk.Separator(self._action_frm, orient="horizontal").pack(fill="x", pady=(0, 8))
        tk.Label(self._action_frm, text="Choisir l'action :",
                 font=("Helvetica", 12, "bold"), bg=BG, fg=BLACK).pack()

        row = tk.Frame(self._action_frm, bg=BG)
        row.pack(pady=5)
        self._action_btns: dict[str, tk.Button] = {}
        for label, key in [("Produits", "produits"), ("Commandes", "commandes"),
                            ("Suivi Commandes", "suivi"), ("Suppression Adresses", "suppr")]:
            b = tk.Button(row, text=label, font=("Helvetica", 10),
                          bg=YELLOW, fg=BLACK, padx=13, pady=6,
                          relief="flat", cursor="hand2",
                          command=lambda k=key: self._select_action(k))
            b.pack(side="left", padx=5)
            self._action_btns[key] = b

    def _build_panels(self):
        self._panel_host = tk.Frame(self._content, bg=BG)
        # pack() déclenché dans _select_action
        self._panels: dict[str, tk.Frame] = {
            k: self._make_panel(k)
            for k in ("produits", "commandes", "suivi", "suppr")
        }

    def _make_panel(self, key: str) -> tk.Frame:
        frm = tk.Frame(self._panel_host, bg=BG, pady=10)

        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=(0, 8))

        _TITLES = {
            "produits":  "Scraping Produits",
            "commandes": "Scraping Commandes",
            "suivi":     "Suivi Commandes",
            "suppr":     "Suppression d'adresses",
        }
        tk.Label(frm, text=_TITLES[key],
                 font=("Helvetica", 13, "bold"), bg=BG, fg=BLACK).pack()

        frm.lbl_site = tk.Label(frm, text="", font=("Helvetica", 9, "italic"),
                                 bg=BG, fg=GRAY)
        frm.lbl_site.pack()

        # Zone de saisie optionnelle
        frm.input_area = tk.Frame(frm, bg=BG)
        frm.input_area.pack(pady=6)

        if key == "produits":
            row = tk.Frame(frm.input_area, bg=BG)
            row.pack()
            tk.Label(row, text="Catégorie :", font=("Helvetica", 10),
                     bg=BG, fg=BLACK).pack(side="left")
            frm.cat_var   = tk.StringVar()
            frm.cat_combo = ttk.Combobox(row, textvariable=frm.cat_var,
                                          state="readonly", width=46,
                                          font=("Helvetica", 10))
            frm.cat_combo.pack(side="left", padx=8)
            frm.cat_row = row

        elif key == "commandes":
            for attr, label in [("entry_from", "Date début  (JJ/MM/AAAA) :"),
                                  ("entry_to",   "Date fin     (JJ/MM/AAAA) :")]:
                row = tk.Frame(frm.input_area, bg=BG)
                row.pack(pady=2)
                tk.Label(row, text=label, font=("Helvetica", 10), bg=BG, fg=BLACK,
                          width=27, anchor="w").pack(side="left")
                e = tk.Entry(row, font=("Helvetica", 10), width=14,
                              insertbackground=BLACK)
                e.pack(side="left", padx=4)
                setattr(frm, attr, e)

        # Boutons
        btns = tk.Frame(frm, bg=BG)
        btns.pack(pady=9)

        frm.btn_launch = tk.Button(btns, text="▶   Lancer",
                                    font=("Helvetica", 11, "bold"),
                                    bg=BTN_GRN, fg=WHITE, padx=14, pady=7,
                                    relief="flat", cursor="hand2",
                                    command=lambda: self._launch(key))
        frm.btn_launch.pack(side="left", padx=4)

        frm.btn_stop = tk.Button(btns, text="■   Arrêter",
                                  font=("Helvetica", 11),
                                  bg=BTN_RED, fg=WHITE, padx=14, pady=7,
                                  relief="flat", cursor="hand2",
                                  command=lambda: self._stop(key))
        frm.btn_stop.pack(side="left", padx=4)

        frm.btn_dl = tk.Button(btns, text="⬇   Télécharger CSV",
                                font=("Helvetica", 11),
                                bg=BTN_BLU, fg=WHITE, padx=14, pady=7,
                                relief="flat", cursor="hand2",
                                command=lambda: self._download(key))
        frm.btn_dl.pack(side="left", padx=4)

        # Indicateur d'état
        srow = tk.Frame(frm, bg=BG)
        srow.pack(pady=3)
        frm.dot = tk.Label(srow, text="●", font=("Helvetica", 15), bg=BG, fg=GRAY)
        frm.dot.pack(side="left")
        frm.lbl_status = tk.Label(srow, text="Non lancé", font=("Helvetica", 10),
                                   bg=BG, fg=GRAY)
        frm.lbl_status.pack(side="left", padx=6)

        frm.lbl_csv = tk.Label(frm, text="", font=("Helvetica", 9),
                                bg=BG, fg=GRAY, wraplength=660, justify="center")
        frm.lbl_csv.pack(pady=(2, 0))

        return frm

    # ─── Navigation ───────────────────────────────────────────────────────────

    def _select_site(self, name: str):
        self._site = name
        self._lbl_site.config(text=f"Site sélectionné : {name}", fg=GREEN_TXT)
        for n, b in self._site_btns.items():
            b.config(bg=YELLOW_ON if n == name else YELLOW,
                     fg=WHITE     if n == name else BLACK)
        for b in self._action_btns.values():
            b.config(bg=YELLOW, fg=BLACK)
        self._action_frm.pack(fill="x")
        for p in self._panels.values():
            p.pack_forget()
        self._panel_host.pack_forget()

    def _select_action(self, key: str):
        self._action = key
        for k, b in self._action_btns.items():
            b.config(bg=YELLOW_ON if k == key else YELLOW,
                     fg=WHITE     if k == key else BLACK)
        self._panel_host.pack(fill="x", pady=(0, 20))
        for p in self._panels.values():
            p.pack_forget()
        panel = self._panels[key]
        panel.pack(fill="x")
        panel.lbl_site.config(text=f"Site : {self._site}")

        if key == "produits":
            cfg = SITES_CONFIG.get(self._site, {})
            if cfg.get("has_categories"):
                panel.cat_row.pack()
                panel.cat_combo["values"] = cfg["categories"]
                if not panel.cat_var.get() and cfg["categories"]:
                    panel.cat_combo.current(0)
            else:
                panel.cat_row.pack_forget()

    # ─── Helpers statut ───────────────────────────────────────────────────────

    def _set_running(self, key: str):
        p = self._panels[key]
        p.dot.config(fg=GREEN_TXT)
        p.lbl_status.config(text="En cours de scraping...", fg=GREEN_TXT)

    def _set_done(self, key: str, msg: str):
        p = self._panels[key]
        p.dot.config(fg=GRAY)
        p.lbl_status.config(text=msg, fg=BLACK if msg != "Non lancé" else GRAY)

    def _set_csv_label(self, key: str, path: str):
        if path and os.path.exists(path):
            self._csv_by_action[key] = path
            self._panels[key].lbl_csv.config(text=f"Fichier CSV : {path}", fg=GREEN_TXT)

    # ─── Contrôles ────────────────────────────────────────────────────────────

    def _launch(self, key: str):
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showwarning("Déjà en cours",
                                   "Un scraper est déjà actif.\nArrêtez-le d'abord.")
            return
        if not self._site:
            messagebox.showerror("Erreur", "Veuillez d'abord choisir un site.")
            return
        try:
            if self._site == "Setin":
                self._launch_setin(key)
            elif self._site == "Legallais":
                self._launch_legallais(key)
            else:
                self._launch_prolians(key)
        except ValueError as exc:
            messagebox.showerror("Saisie invalide", str(exc))
        except Exception as exc:
            messagebox.showerror("Erreur", str(exc))

    def _stop(self, key: str):
        self._running = False
        if self._scraper and hasattr(self._scraper, "request_stop"):
            self._scraper.request_stop()
        if self._async_loop and self._async_task:
            self._async_loop.call_soon_threadsafe(self._async_task.cancel)
        if self._worker_thread and self._worker_thread.is_alive():
            try:
                import ctypes
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(self._worker_thread.ident),
                    ctypes.py_object(KeyboardInterrupt),
                )
            except Exception:
                pass
        self._set_done(key, "Arrêt demandé...")

    def _download(self, key: str):
        if key == "suppr":
            messagebox.showinfo("Non disponible",
                                "Aucun export CSV pour la suppression d'adresses.")
            return

        if not self._site:
            messagebox.showerror("Erreur", "Veuillez d'abord choisir un site.")
            return

        # Map GUI site name → sqlite_db site key
        _SITE_KEYS = {"Setin": "setin", "Legallais": "legallais", "Prolians": "prolians"}
        site_key = _SITE_KEYS.get(self._site, self._site.lower())

        # Map action → (table suffix, CSV headers)
        _ACTION_INFO = {
            "produits":  ("products",  CSV_HEADERS),
            "commandes": ("orders",    ORDERS_CSV_HEADERS),
            "suivi":     ("tracking",  TRACKING_CSV_HEADERS),
        }
        table_suffix, headers = _ACTION_INFO[key]
        table = f"{site_key}_{table_suffix}"

        # Read all rows from SQLite and write to a temporary CSV
        try:
            from db.sqlite_db import init_site_db, export_table_to_csv
            conn = init_site_db(site_key)
            with tempfile.NamedTemporaryFile(
                suffix=".csv", dir=str(CSV_DIR), delete=False
            ) as _tmp:
                tmp_path = Path(_tmp.name)
            CSV_DIR.mkdir(parents=True, exist_ok=True)
            n_rows = export_table_to_csv(conn, table, headers, tmp_path)
            conn.close()
        except Exception as exc:
            messagebox.showerror("Erreur base de données",
                                 f"Impossible de lire {table} :\n{exc}")
            return

        if n_rows == 0:
            messagebox.showinfo("Base vide",
                                f"Aucune donnée dans {table}.\n"
                                "Lancez d'abord le scraper pour peupler la base.")
            if tmp_path.exists():
                tmp_path.unlink()
            return

        run_ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        default_name = f"export_{table}_{run_ts}.csv"
        dest = filedialog.asksaveasfilename(
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Tous les fichiers", "*.*")],
        )
        if dest:
            shutil.copy2(tmp_path, dest)
            messagebox.showinfo("Enregistré",
                                f"{n_rows} ligne(s) exportée(s) :\n{dest}")
        if tmp_path.exists():
            tmp_path.unlink()

    # ─── Lanceurs Setin ───────────────────────────────────────────────────────

    def _launch_setin(self, key: str):
        panel = self._panels[key]
        cfg   = SITES_CONFIG["Setin"]

        if key == "produits":
            cat     = panel.cat_var.get()
            mod     = import_module(cfg["imports"]["produits"])
            scraper = mod.create_scraper(category_name=cat)
            self._start_async(key, scraper.run(), scraper,
                              lambda: str(scraper._csv_path or ""))

        elif key == "commandes":
            df, dt  = self._read_dates(panel)
            mod     = import_module(cfg["imports"]["commandes"])
            scraper = mod.create_scraper(date_from=df, date_to=dt)
            self._start_async(key, scraper.run(), scraper,
                              lambda: str(scraper._csv_path or ""))

        elif key == "suivi":
            now     = datetime.now()
            mod     = import_module(cfg["imports"]["suivi"])
            scraper = mod.create_scraper(date_from=now - timedelta(days=7), date_to=now)
            self._start_async(key, scraper.run(), scraper,
                              lambda: str(scraper._csv_path or ""))

        elif key == "suppr":
            mod     = import_module(cfg["imports"]["suppr"])
            scraper = mod.create_scraper()
            self._start_async(key, scraper.run(), scraper, lambda: "")

    # ─── Lanceurs Prolians ────────────────────────────────────────────────────

    def _launch_prolians(self, key: str):
        panel = self._panels[key]
        cfg   = SITES_CONFIG["Prolians"]

        if key == "produits":
            mod     = import_module(cfg["imports"]["produits"])
            scraper = mod.create_scraper()
            self._start_async(key, scraper.run(), scraper,
                              lambda: self._latest_csv("scrap_p3_products_*.csv"))

        elif key == "commandes":
            df, dt = self._read_dates(panel)
            self._start_sync(key,
                             lambda: _run_prolians_orders_sync(df, dt),
                             lambda: "")

        elif key == "suivi":
            mod     = import_module(cfg["imports"]["suivi"])
            scraper = mod.create_scraper()
            self._start_async(key, scraper.run(), scraper, lambda: "")

        elif key == "suppr":
            mod = import_module(cfg["imports"]["suppr"])
            self._start_sync(key, mod.main, lambda: "")

    # ─── Lanceurs Legallais ───────────────────────────────────────────────────

    def _launch_legallais(self, key: str):
        panel = self._panels[key]
        cfg   = SITES_CONFIG["Legallais"]

        if key == "produits":
            cat = panel.cat_var.get() if cfg.get("has_categories") else ""
            category_filter = cat if cat else None
            self._start_sync(
                key,
                lambda: _run_legallais_products_sync(category_filter),
                lambda: self._latest_csv("scrap_p1_products_*.csv"),
            )

        elif key == "commandes":
            df, dt = self._read_dates(panel)
            self._start_sync(
                key,
                lambda: _run_legallais_orders_sync(df, dt),
                lambda: "",
            )

        elif key == "suivi":
            mod = import_module(cfg["imports"]["suivi"])
            self._start_sync(
                key,
                mod.main,
                lambda: "",
            )

        elif key == "suppr":
            mod = import_module(cfg["imports"]["suppr"])
            self._start_sync(key, mod.cleanup_legallais_addresses, lambda: "")

    # ─── Helpers threading ────────────────────────────────────────────────────

    def _read_dates(self, panel) -> tuple[datetime, datetime]:
        df_str = panel.entry_from.get().strip()
        dt_str = panel.entry_to.get().strip()
        if not df_str or not dt_str:
            raise ValueError("Veuillez renseigner les deux dates.")
        df = _parse_date(df_str)
        dt = _parse_date(dt_str)
        if df > dt:
            raise ValueError("La date de début doit être ≤ la date de fin.")
        return df, dt

    def _latest_csv(self, pattern: str) -> str:
        files = sorted(CSV_DIR.glob(pattern),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        return str(files[0]) if files else ""

    def _start_async(self, key: str, coro, scraper, get_path):
        """Lance un scraper asyncio dans un thread daemon (Setin, Prolians produits/suivi)."""
        self._scraper = scraper
        self._running = True
        self._set_running(key)

        def _worker():
            # Chaque thread a sa propre boucle asyncio (Tkinter reste sur le thread principal)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._async_loop = loop
            try:
                task = loop.create_task(coro)
                self._async_task = task
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            finally:
                self._async_loop = None
                self._async_task = None
                loop.close()
                was_running = self._running
                self._running = False
                path = get_path()
                self.after(0, lambda: self._on_done(key, path, was_running))

        self._worker_thread = threading.Thread(target=_worker, daemon=True)
        self._worker_thread.start()

    def _start_sync(self, key: str, func, get_path):
        """Lance une fonction synchrone bloquante dans un thread (Playwright sync, Botasaurus)."""
        self._scraper = None
        self._running = True
        self._set_running(key)

        def _worker():
            try:
                func()
            except BaseException:
                # KeyboardInterrupt injecté par _stop() ou erreur scraper — ne pas crasher l'UI
                pass
            finally:
                was_running = self._running
                self._running = False
                try:
                    path = get_path()
                except Exception:
                    path = ""
                self.after(0, lambda: self._on_done(key, path, was_running))

        self._worker_thread = threading.Thread(target=_worker, daemon=True)
        self._worker_thread.start()

    def _on_done(self, key: str, csv_path: str, was_running: bool):
        self._running = False
        self._scraper = None
        if was_running:
            self._set_done(key, "Terminé ✔")
        self._set_csv_label(key, csv_path)
