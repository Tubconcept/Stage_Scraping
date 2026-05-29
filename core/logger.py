import logging
import traceback
from datetime import datetime
from core.config import LOG_DIR, IGNORED_ERRORS


def setup_logger(name: str) -> logging.Logger:
    """Configure un logger structuré"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # Créer le répertoire log s'il n'existe pas
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Handler fichier
    log_file = LOG_DIR / f"scraper-{datetime.today().strftime('%Y-%m-%d')}.log"
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # Handler console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Format
    formatter = logging.Formatter(
        '[%(asctime)s] %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    
    return logger

logger = setup_logger(__name__)


def log_exception(log: logging.Logger, exception: Exception, context: str = "") -> None:
    """
    Log les exceptions de façon structurée

    Args:
        log: Logger à utiliser
        exception: Exception à logger
        context: Contexte additionnel
    """
    # Vérifier si c'est une erreur à ignorer
    if any(msg in str(exception) for msg in IGNORED_ERRORS):
        return

    log.error(f"Exception: {type(exception).__name__}: {str(exception)}")
    if context:
        log.error(f"Contexte: {context}")

    # Tracer la stack
    tb = traceback.extract_tb(exception.__traceback__)
    for frame in tb:
        log.debug(
            f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}'
        )
        log.debug(f"    {frame.line}")
