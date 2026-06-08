"""
Point d'entrée principal du projet Stage_Scraping.

Ce script lance l'interface graphique Tkinter (gui/interface.py) qui permet de :
  - Choisir un fournisseur (Setin P5, Legallais P1, Prolians P3)
  - Lancer le scraping produits, commandes, suivi ou suppression d'adresses
  - Exporter les données depuis SQLite vers un fichier CSV

Lancement :
    python main.py
    # ou via setup.bat / uv run python main.py
"""

from gui.interface import ScraperApp

if __name__ == "__main__":
    # Instanciation et boucle événementielle Tkinter (bloquante jusqu'à fermeture)
    app = ScraperApp()
    app.mainloop()
