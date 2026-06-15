"""
Configuration du logging structuré pour tous les scrapers.

Chaque scraper obtient un logger nommé « {fournisseur}.{tâche} » via setup_logger().
Les messages DEBUG vont dans un fichier journalier ; INFO+ s'affichent aussi en console.
"""

import logging
import traceback
from datetime import datetime
from core.config import LOG_DIR, IGNORED_ERRORS


def setup_logger(name: str) -> logging.Logger:
    """Configure et retourne un logger nommé pour un scraper ou module.

    Args:
        name: Identifiant du logger (ex. « setin.products », « prolians.orders »).

    Returns:
        Logger Python prêt à l'emploi (handlers fichier + console).
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Un fichier par site par jour : log/legallais-2026-06-15.log
    site = name.split(".")[0]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{site}-{datetime.today().strftime('%Y-%m-%d')}.log"
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)

    # Console : niveau INFO pour ne pas noyer l'utilisateur
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        '[%(asctime)s] %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Évite les handlers dupliqués si setup_logger est rappelé
    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger

# Logger par défaut du package core (peu utilisé directement)
logger = setup_logger(__name__)


def log_exception(log: logging.Logger, exception: Exception, context: str = "") -> None:
    """Enregistre une exception avec contexte et stack trace (niveau DEBUG).

    Les erreurs listées dans IGNORED_ERRORS (fermeture navigateur) sont ignorées.

    Args:
        log: Logger du scraper en cours.
        exception: Exception capturée.
        context: Texte libre (URL, référence produit, étape du flux…).
    """
    if any(msg in str(exception) for msg in IGNORED_ERRORS):
        return

    log.error(f"Exception: {type(exception).__name__}: {str(exception)}")
    if context:
        log.error(f"Contexte: {context}")

    # Détail fichier/ligne pour le diagnostic en fichier log uniquement
    tb = traceback.extract_tb(exception.__traceback__)
    for frame in tb:
        log.debug(
            f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}'
        )
        log.debug(f"    {frame.line}")
