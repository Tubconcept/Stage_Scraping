"""Classe de base pour tous les scrapers async avec gestion Playwright."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

from .logger import setup_logger
from .config import VIEWPORT


class BaseScraper:
    """Classe de base pour les scrapers Playwright async.

    Gère:
    - Cycle de vie du browser et contexte
    - Persistance de session storage (.json)
    - Logging structuré
    - Signaux d'arrêt gracieux (SIGINT, SIGTERM)
    """

    SUPPLIER: str = "unknown"

    def __init__(self, task_name: str) -> None:
        """Initialise le scraper.

        Args:
            task_name: Nom descriptif de la tâche (pour logs).
        """
        self.task_name = task_name
        self.log = setup_logger(f"{self.SUPPLIER}.{task_name}")

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._pages: list[Page] = []

        self._stop_requested = False
        self._setup_signal_handlers()

    # ──────────────────────────────────────────────────────────────────
    # Gestion du cycle de vie Playwright
    # ──────────────────────────────────────────────────────────────────

    async def start_browser(
        self,
        headless: bool = True,
        storage_state: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Lance le navigateur Playwright.

        Args:
            headless: Mode headless (défaut: True).
            storage_state: Chemin vers un fichier JSON de session à restaurer.
            **kwargs: Arguments supplémentaires pour new_context().
        """
        try:
            self._playwright = await async_playwright().start()
            self.log.debug("Playwright démarré")

            self._browser = await self._playwright.chromium.launch(headless=headless)
            self.log.debug("Browser Chromium lancé")

            context_args: dict = {"viewport": VIEWPORT, **kwargs}
            if storage_state:
                context_args["storage_state"] = storage_state
                self.log.debug("Session restaurée depuis : %s", storage_state)

            self._context = await self._browser.new_context(**context_args)
            self.log.debug("Context créé")
            self.log.info("Browser lancé avec succès")

        except Exception as exc:
            self.log.error(f"Erreur démarrage browser: {exc}")
            raise

    async def new_page(self) -> Page:
        """Crée une nouvelle page dans le contexte."""
        if not self._context:
            raise RuntimeError("Context not initialized. Call start_browser() first.")

        page = await self._context.new_page()
        self._pages.append(page)
        self.log.debug(f"Page créée (total: {len(self._pages)})")
        return page

    async def close(self) -> None:
        """Ferme toutes les ressources (pages, context, browser, playwright)."""
        # Fermer les pages
        for page in self._pages:
            try:
                await page.close()
            except Exception:
                pass
        self._pages.clear()

        # Fermer le context
        if self._context:
            try:
                await self._context.close()
                self.log.debug("Context fermé")
            except Exception:
                pass
            self._context = None

        # Fermer le browser
        if self._browser:
            try:
                await self._browser.close()
                self.log.debug("Browser fermé")
            except Exception:
                pass
            self._browser = None

        # Arrêter Playwright
        if self._playwright:
            try:
                await self._playwright.stop()
                self.log.debug("Playwright arrêté")
            except Exception:
                pass
            self._playwright = None

        self.log.info("Ressources fermées avec succès")

    # ──────────────────────────────────────────────────────────────────
    # Persistance de session storage
    # ──────────────────────────────────────────────────────────────────

    async def save_storage_state(self, path: Optional[Path | str]) -> None:
        """Sauvegarde l'état du contexte (cookies, localStorage, etc.).

        Args:
            path: Chemin où sauvegarder. Si None, ignore.
        """
        if not path or not self._context:
            return

        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)

            storage = await self._context.storage_state()
            import json

            with open(path, "w", encoding="utf-8") as f:
                json.dump(storage, f, indent=2)

            self.log.debug(f"État stocké: {path}")
        except Exception as exc:
            self.log.error(f"Erreur sauvegarde session: {exc}")

    # ──────────────────────────────────────────────────────────────────
    # Contrôle d'arrêt
    # ──────────────────────────────────────────────────────────────────

    def _setup_signal_handlers(self) -> None:
        """Configure les gestionnaires de signaux pour arrêt gracieux."""

        def handle_signal(signum, frame):
            self.log.warning(f"Signal {signum} reçu — arrêt en cours...")
            self._stop_requested = True

        try:
            signal.signal(signal.SIGINT, handle_signal)
            signal.signal(signal.SIGTERM, handle_signal)
        except ValueError:
            # Sur Windows, certains signaux ne sont pas supportés
            pass

    def should_stop(self) -> bool:
        """Retourne True si un arrêt a été demandé."""
        return self._stop_requested

    def request_stop(self) -> None:
        """Demande l'arrêt gracieux du scraper."""
        self.log.warning("Arrêt demandé")
        self._stop_requested = True

    # ──────────────────────────────────────────────────────────────────
    # Utilitaires
    # ──────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Point d'entrée principal — à implémenter dans les sous-classes."""
        raise NotImplementedError(
            f"{self.__class__.__name__} doit implémenter run()"
        )
