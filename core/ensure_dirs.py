"""
Création des répertoires de travail au démarrage.

Appelé une fois au lancement d'un scraper pour garantir que log/, csv/
et json/ existent avant toute écriture de fichier.
"""

from core.config import LOG_DIR, CSV_DIR, JSON_DIR


def ensure_dirs():
    """Crée log/, csv/ et json/ s'ils n'existent pas encore (parents inclus)."""
    for directory in (LOG_DIR, CSV_DIR, JSON_DIR):
        directory.mkdir(parents=True, exist_ok=True)
