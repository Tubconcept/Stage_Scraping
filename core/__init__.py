"""
Package core — infrastructure partagée par tous les scrapers.

Modules :
  - config      : constantes globales (CSV, timeouts, chemins)
  - base_scraper: classe de base Playwright async
  - logger      : journalisation fichier + console
  - utils       : nettoyage texte, extraction DOM sécurisée
  - ensure_dirs : création des dossiers log/, csv/, json/
"""

from .ensure_dirs import ensure_dirs

# Crée les répertoires de travail dès l'import du package
ensure_dirs()
