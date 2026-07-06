"""
Scraper Sider (mode : catalogue léger COMPLET via sitemap + HTTP authentifié).

Énumère TOUT le catalogue via `sider_sitemap` (~23 900 fiches `/produit/`), puis
récupère chaque fiche par **GET HTTP authentifié** (cookies de session) et parse :
  - le **JSON-LD** (`<script type="application/ld+json">`) → nom, marque, catégorie,
    description, image, disponibilité ;
  - `span.article-code[data-code]` → référence article (`product_reference_fournisseur`) ;
  - `div.container-price span.price` → **prix compte P6 net HT** (≠ prix public du
    JSON-LD, souvent ~−58 %) → `normalize_price`.

Tout en HTTP concurrent (ThreadPool), **sans navigateur par produit** : ~minutes au
lieu de jours de DOM Playwright. Un seul login Playwright au départ pour obtenir les
cookies (réutilise `SiderProductScraper._ensure_session`). Les déclinaisons détaillées
(table articles) restent du ressort du mode DOM « Produits ».

Robustesse anti-blocage (piste 1)
---------------------------------
Historiquement, la session Sider était coupée côté serveur après quelques centaines de
requêtes concurrentes : toutes les pages suivantes étaient servies **déconnectées**
(sans prix compte ni `article-code`) et comptées « sans données », d'où un catalogue
qui plafonnait très en dessous du réel. Trois garde-fous :
  1. **Détection page-déconnectée** — la présence du lien « Création de compte/Connexion »
     (visible seulement pour un visiteur anonyme) signale une session perdue.
  2. **Re-login à chaud** — un seul thread relance le login Playwright, remplace les
     cookies partagés et incrémente une génération ; les autres threads repartent sur
     les cookies frais. Anti-rafale par intervalle minimal entre deux relogins.
  3. **Arrêt sur mur (stop-on-cliff)** — au-delà de N pages « bloquées » consécutives
     (ban IP non récupérable) ou de re-logins inefficaces, on arrête au lieu de brûler
     tout le sitemap. Les URLs non traitées sont écrites dans `output/sider_manquants.txt`
     pour une relance ciblée.

Factory GUI : ``create_scraper() -> SiderLightSitemapScraper``.
CLI         : ``python scrap_sider_light_sitemap.py [--limit N]``.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from core.config import PROFILES_DIR
from core.logger import setup_logger
from core.polite_http import build_browser_headers, polite_get
from core.utils import normalize_price
from db.mariadb_db import init_site_db, insert_product, update_product_fields
from scrapers.Sider_P6.products.sider_sitemap import collect_product_urls
from scrapers.Sider_P6.products.scrap_sider_products import SiderProductScraper

load_dotenv(PROJECT_ROOT / ".env")
log = setup_logger("sider.products")

_TIMEOUT = 30
# Défauts anti-détection (configurables depuis la GUI / CLI). Concurrence bornée +
# délai jittered (dans polite_get) = pas de rafale. Débit ≈ workers / délai_moyen.
# Volontairement PRUDENTS : le mur des ~700 requêtes était corrélé à une concurrence
# élevée (10 workers, délai 0,3-0,9 s). Moins vite = session qui vit bien plus longtemps.
_MAX_WORKERS = 3
_DELAY = (1.0, 3.0)
_REFERER = "https://www.sider.biz/"

# ─── Robustesse session / anti-blocage ───────────────────────────────────────
# Pause après un re-login réussi, le temps que la pression retombe avant de reprendre.
_RELOGIN_COOLDOWN = 5.0
# Intervalle minimal entre deux relogins : en-deçà, un nouveau blocage = relogin
# « inefficace » (probable ban IP, pas une simple expiration de cookie).
_RELOGIN_MIN_INTERVAL = 20.0
# Nb de relogins inefficaces consécutifs → arrêt.
_MAX_RELOGIN_FAILSTREAK = 3
# Nb de pages « bloquées » consécutives (non récupérables) → arrêt du run.
_CLIFF_CONSECUTIVE = 40
# Fichier des URLs non traitées (bloquées / échecs réseau) pour relance ciblée.
_MANQUANTS_PATH = PROJECT_ROOT / "output" / "sider_manquants.txt"

# Statuts renvoyés par _fetch_one / _extract.
_OK, _EMPTY, _BLOCKED, _MISS = "ok", "empty", "blocked", "miss"


class SiderLightSitemapScraper:
    """Catalogue léger COMPLET Sider : sitemap + GET HTTP authentifié (JSON-LD + prix compte)."""

    def __init__(self, limit: int | None = None, max_workers: int = _MAX_WORKERS,
                 delay: tuple[float, float] = _DELAY) -> None:
        self._limit = limit
        self._max_workers = max(1, int(max_workers))
        self._delay = delay
        self._stop_requested = False

        # État session partagé entre workers (protégé par _session_lock).
        self._headers: dict = {}
        self._session_gen = 0                  # incrémenté à chaque re-login réussi
        self._session_lock = threading.Lock()
        self._last_relogin_ts: float | None = None
        self._relogin_fail_streak = 0

    def request_stop(self) -> None:
        self._stop_requested = True

    async def run(self) -> None:
        cookie, ua = await self._login_and_get_cookie()
        if not cookie:
            log.error("Session Sider indisponible — arrêt.")
            return
        self._headers = build_browser_headers(ua=ua, cookie=cookie, referer=_REFERER)
        self._session_gen = 0
        await asyncio.to_thread(self._sync_fetch)

    # ─── Session (login Playwright → cookies + UA) ────────────────────────────

    async def _login_and_get_cookie(self) -> tuple[str, str]:
        """Connexion via ``SiderProductScraper`` → (en-tête Cookie, User-Agent de session).

        On récupère AUSSI l'``User-Agent`` du navigateur de login pour que les GET
        HTTP suivants soient cohérents avec les cookies (anti-détection). Utilisé au
        démarrage (boucle principale) ET à chaud pour le re-login (thread worker,
        via ``asyncio.run``).
        """
        sc = SiderProductScraper()
        storage_path = PROFILES_DIR / "sider" / "session.json"
        storage = str(storage_path) if storage_path.exists() else None
        raw: list[dict] = []
        ua = ""
        try:
            await sc.start_browser(headless=True, storage_state=storage)
            page = await sc.new_page()
            await sc._ensure_session(page, storage_path)
            raw = await sc._context.cookies()
            ua = await page.evaluate("() => navigator.userAgent")
        except Exception as exc:
            log.error("Connexion Sider échouée : %s", exc)
            return "", ""
        finally:
            try:
                await sc.close()
            except Exception:
                pass
        cookie = "; ".join(f"{c['name']}={c['value']}" for c in raw if "sider" in (c.get("domain") or ""))
        return cookie, ua

    def _register_relogin_failure(self, reason: str) -> bool:
        """Comptabilise un re-login raté/inefficace ; arrête le run au bout de N. Retourne False.

        (Appelé sous ``_session_lock``.)
        """
        self._relogin_fail_streak += 1
        log.warning("Re-login KO (%d/%d) : %s",
                    self._relogin_fail_streak, _MAX_RELOGIN_FAILSTREAK, reason)
        if self._relogin_fail_streak >= _MAX_RELOGIN_FAILSTREAK:
            log.error("Blocage non récupérable (ban IP probable) — arrêt.")
            self._stop_requested = True
        return False

    def _recover_session(self, stale_gen: int) -> bool:
        """Re-login à chaud, thread-safe. Retourne True si la session est de nouveau valide.

        - Si un autre worker a déjà rafraîchi la session depuis que CE thread a observé
          l'expiration (génération avancée) → rien à faire, True.
        - Sinon, un seul thread à la fois relance le login Playwright, remplace les
          cookies partagés et incrémente la génération.
        - Anti-rafale : deux relogins rapprochés (< _RELOGIN_MIN_INTERVAL) signalent un
          relogin *inefficace* (probable ban IP) → failstreak, puis arrêt au bout de N.
        """
        with self._session_lock:
            if self._session_gen != stale_gen:
                return True  # déjà relogué par un autre worker
            if self._stop_requested:
                return False

            now = time.monotonic()
            if self._last_relogin_ts is not None and (now - self._last_relogin_ts) < _RELOGIN_MIN_INTERVAL:
                # Un relogin très récent n'a pas suffi → blocage plus dur qu'une simple
                # expiration de cookie (probable IP).
                return self._register_relogin_failure(
                    f"blocage rapproché < {_RELOGIN_MIN_INTERVAL:.0f}s")

            log.warning("Session Sider expirée — re-login (génération %d → %d)…",
                        self._session_gen, self._session_gen + 1)
            try:
                cookie, ua = asyncio.run(self._login_and_get_cookie())
            except Exception as exc:
                return self._register_relogin_failure(f"exception {exc}")
            if not cookie:
                return self._register_relogin_failure("cookies vides")

            self._headers = build_browser_headers(ua=ua, cookie=cookie, referer=_REFERER)
            self._session_gen += 1
            self._last_relogin_ts = time.monotonic()
            self._relogin_fail_streak = 0
            log.info("Re-login OK (génération %d) — reprise dans %.0fs.",
                     self._session_gen, _RELOGIN_COOLDOWN)
            time.sleep(_RELOGIN_COOLDOWN)
            return True

    # ─── Parsing fiche ────────────────────────────────────────────────────────

    def _parse_soup(self, soup: BeautifulSoup, url: str) -> dict | None:
        """Extrait la ligne produit d'une page CONNECTÉE (soup déjà construit)."""
        name = desc = brand = category = image = avail = ""
        tag = soup.find("script", attrs={"type": "application/ld+json"})
        if tag and tag.string:
            try:
                data = json.loads(tag.string)
                items = data if isinstance(data, list) else [data]
                prod = next((x for x in items if isinstance(x, dict) and x.get("@type") == "Product"), None)
                if prod:
                    name = prod.get("name", "") or ""
                    desc = prod.get("description", "") or ""
                    b = prod.get("brand")
                    brand = b.get("name", "") if isinstance(b, dict) else (b or "")
                    category = prod.get("category", "") or ""
                    img = prod.get("image", "")
                    image = img if isinstance(img, str) else (img[0] if isinstance(img, list) and img else "")
                    avail = ((prod.get("offers") or {}).get("availability") or "").rsplit("/", 1)[-1]
            except Exception:
                pass
        ac = soup.select_one("span.article-code")
        ref = ((ac.get("data-code") or ac.get_text(strip=True)) if ac else "").strip()
        pe = soup.select_one("div.container-price span.price")
        price = normalize_price(pe.get_text()) if pe else ""
        if not (ref or name):
            return None
        cat_tree = "||".join(p.strip() for p in category.split("/") if p.strip())
        stock = "disponible" if avail == "InStock" else ("non disponible" if avail else "")
        return {
            "product_fournisseur":           "P6",
            "product_reference_fournisseur": ref,
            "product_designation":           name,
            "product_description":           desc,
            "product_brand":                 brand,
            "product_category_tree":         cat_tree,
            "product_image_url":             image,
            "product_price_ht":              price,
            "product_stock_status":          stock,
            "product_fournisseur_url":       url,
        }

    @staticmethod
    def _is_logged_out(soup: BeautifulSoup) -> bool:
        """True si la page est servie DÉCONNECTÉE (session perdue / anti-bot).

        Signal : le lien « Création de compte/Connexion » n'apparaît QUE pour un
        visiteur anonyme (cf. SiderProductScraper._connexion, qui déduit la session
        active de l'ABSENCE de cette icône). Sa présence = cookies invalidés.
        """
        return soup.select_one("a[title='Création de compte/Connexion']") is not None

    def _extract(self, html: str, url: str) -> tuple[str, object]:
        """Classe une page téléchargée. Un seul parse BeautifulSoup.

        Retourne (statut, payload) :
          (_OK, row)      → fiche enrichie
          (_BLOCKED, url) → page déconnectée : session à récupérer
          (_EMPTY, url)   → page servie mais sans données produit (obsolète/retirée)
        """
        soup = BeautifulSoup(html, "html.parser")
        # L'ordre importe : une page déconnectée peut CONTENIR un JSON-LD au prix
        # PUBLIC ; il ne faut surtout pas le persister à la place du prix compte P6.
        if self._is_logged_out(soup):
            return (_BLOCKED, url)
        row = self._parse_soup(soup, url)
        if row:
            return (_OK, row)
        return (_EMPTY, url)

    def _fetch_one(self, url: str) -> tuple[str, object]:
        """GET poli + classement. Un réessai après re-login si la page est déconnectée."""
        for _attempt in range(2):  # 1 essai + 1 réessai après récupération de session
            if self._stop_requested:
                return (_BLOCKED, url)
            gen = self._session_gen
            headers = self._headers  # lecture atomique (GIL) d'une réf de dict valide
            html = polite_get(url, headers, timeout=_TIMEOUT, delay=self._delay,
                              should_stop=lambda: self._stop_requested)
            if html is None:
                return (_MISS, url)  # échec réseau / 403-503 persistant après retries
            status, payload = self._extract(html, url)
            if status == _BLOCKED:
                if self._recover_session(gen):
                    continue         # réessaie avec des cookies frais
                return (_BLOCKED, url)
            return (status, payload)
        return (_BLOCKED, url)

    def _persist(self, row: dict) -> str:
        """UPSERT par URL (identité unique en base), sinon INSERT.

        Met à jour uniquement les champs « light » de la fiche existante — en
        préservant les champs riches déjà posés par un run DOM (desc longue, images,
        docs, déclinaisons). On matche par URL et NON par réf : une réf peut être
        partagée par plusieurs fiches (~697 cas en base), ce qui collisionne avec
        l'index UNIQUE ``product_fournisseur_url`` si l'on met à jour par réf.
        """
        url = row.get("product_fournisseur_url", "")
        if url and update_product_fields(None, "sider", url, row,
                                         match_col="product_fournisseur_url"):
            return "maj"
        try:
            insert_product(None, "sider", row)
        except Exception:
            pass
        return "new"

    def _write_manquants(self, urls: list[str]) -> None:
        """Écrit les URLs non traitées (bloquées / échecs) pour une relance ciblée."""
        if not urls:
            return
        try:
            _MANQUANTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_MANQUANTS_PATH, "w", encoding="utf-8") as fh:
                fh.write("\n".join(urls))
            log.info("%d URL(s) non traitée(s) écrites dans %s", len(urls), _MANQUANTS_PATH)
        except Exception as exc:
            log.warning("Écriture des manquants impossible : %s", exc)

    # ─── Boucle HTTP concurrente ──────────────────────────────────────────────

    def _handle_result(self, status: str, payload, stats: dict, manquants: list[str]) -> bool:
        """Met à jour les compteurs d'un résultat. Retourne True s'il faut arrêter (mur)."""
        if status == _OK:
            self._persist(payload)
            stats["ok"] += 1
            stats["streak"] = 0
        elif status == _EMPTY:
            stats["empty"] += 1
            stats["streak"] = 0
        else:  # _BLOCKED ou _MISS → URL à retenter
            manquants.append(payload)
            if status == _BLOCKED:
                stats["blocked"] += 1
                stats["streak"] += 1
            else:
                stats["miss"] += 1
            if stats["streak"] >= _CLIFF_CONSECUTIVE:
                log.error(
                    "Mur de %d pages bloquées consécutives — arrêt (anti-bot/IP). "
                    "Relance ciblée possible via les manquants.", stats["streak"])
                self._stop_requested = True
                return True
        return False

    @staticmethod
    def _pending_urls(futs: dict, manquants: list[str]) -> list[str]:
        """URLs dont le future n'a pas produit de résultat (annulé / non terminé)."""
        already = set(manquants)
        pending: list[str] = []
        for f, u in futs.items():
            if u not in already and (f.cancelled() or not f.done()):
                pending.append(u)
                already.add(u)
        return pending

    def _sync_fetch(self) -> None:
        try:
            init_site_db("sider")
        except Exception as exc:
            log.error("Base MariaDB Sider non initialisée : %s", exc)
            return
        urls = collect_product_urls(logger=log, limit=self._limit)
        if not urls:
            log.warning("Aucune URL produit (sitemap) — arrêt.")
            return
        log.info("Sider : %d produit(s) à enrichir (HTTP poli, %d workers, délai %.1f-%.1f s)",
                 len(urls), self._max_workers, self._delay[0], self._delay[1])

        stats = {"ok": 0, "empty": 0, "blocked": 0, "miss": 0, "streak": 0}
        manquants: list[str] = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as ex:
            futs = {ex.submit(self._fetch_one, u): u for u in urls}
            try:
                for fut in as_completed(futs):
                    if self._stop_requested:
                        log.info("Arrêt demandé.")
                        break
                    status, payload = fut.result()
                    if self._handle_result(status, payload, stats, manquants):
                        break
                    done = stats["ok"] + stats["empty"] + stats["blocked"] + stats["miss"]
                    if done % 250 == 0:
                        log.info("  %d/%d traités (%d enrichis, %d vides, %d bloqués, %d échecs)",
                                 done, len(urls), stats["ok"], stats["empty"],
                                 stats["blocked"], stats["miss"])
            finally:
                ex.shutdown(wait=False, cancel_futures=True)

        # URLs jamais traitées (annulées par l'arrêt) → à retenter elles aussi.
        if self._stop_requested:
            manquants.extend(self._pending_urls(futs, manquants))

        self._write_manquants(manquants)
        log.info("Terminé : %d enrichis, %d vides, %d bloqués, %d échecs (%d à retenter)",
                 stats["ok"], stats["empty"], stats["blocked"], stats["miss"], len(manquants))


def create_scraper(max_workers: int = _MAX_WORKERS,
                   delay: tuple[float, float] = _DELAY) -> SiderLightSitemapScraper:
    """Fabrique attendue par la GUI (réglages anti-détection configurables)."""
    return SiderLightSitemapScraper(max_workers=max_workers, delay=delay)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Catalogue léger COMPLET Sider (sitemap + HTTP authentifié).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limiter le nombre de produits (test).")
    parser.add_argument("--workers", type=int, default=_MAX_WORKERS,
                        help=f"Concurrence (défaut {_MAX_WORKERS}).")
    parser.add_argument("--delay-min", type=float, default=_DELAY[0],
                        help="Délai aléatoire min par requête (s).")
    parser.add_argument("--delay-max", type=float, default=_DELAY[1],
                        help="Délai aléatoire max par requête (s).")
    args = parser.parse_args()
    asyncio.run(SiderLightSitemapScraper(
        limit=args.limit, max_workers=args.workers,
        delay=(args.delay_min, args.delay_max)).run())


if __name__ == "__main__":
    main()
