from core.config import LOG_DIR, CSV_DIR, JSON_DIR


def ensure_dirs():
    for directory in (LOG_DIR, CSV_DIR, JSON_DIR):
        directory.mkdir(parents=True, exist_ok=True)

