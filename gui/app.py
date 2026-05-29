"""Point d'entrée principal de l'interface graphique."""

import tkinter as tk

from gui.theme import BG_GRAY
from gui.pages.page_site   import PageSite
from gui.pages.page_mode   import PageMode
from gui.pages.page_config import PageConfig


class AppState:
    """État partagé entre toutes les pages."""

    def __init__(self):
        self.site_id:        str | None = None
        self.mode_id:        str | None = None
        self.is_running:     bool       = False
        self.active_scraper             = None


class NavigationController:
    """Gère l'affichage des pages (frame switching via tkraise)."""

    def __init__(self, container: tk.Widget, state: AppState):
        self._container = container
        self._state     = state
        self._pages: dict[str, tk.Frame] = {}

    def register(self, name: str, page: tk.Frame):
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._pages[name] = page

    def show(self, name: str):
        page = self._pages[name]
        if hasattr(page, "on_show"):
            page.on_show()
        page.tkraise()


class ScraperApp:
    """Contrôleur principal — initialise la fenêtre, les pages et la navigation."""

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Interface Scraping")
        root.geometry("950x660")
        root.resizable(False, False)
        root.configure(bg=BG_GRAY)

        state     = AppState()
        container = tk.Frame(root, bg=BG_GRAY)
        container.pack(fill="both", expand=True)

        nav = NavigationController(container, state)

        nav.register("site",   PageSite(container, state, nav))
        nav.register("mode",   PageMode(container, state, nav))
        nav.register("config", PageConfig(container, state, nav))

        nav.show("site")


def main():
    root = tk.Tk()
    ScraperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
