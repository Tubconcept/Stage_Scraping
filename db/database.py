"""Initialisation de la base de données Setin."""

from pathlib import Path
from peewee import SqliteDatabase
from core.config import DB_PATH
from .models import db, BaseModel, SetinOrder, SetinProduct, SetinTracking


def _get_table_columns(sqlite_db: SqliteDatabase, table_name: str) -> set[str]:
    """Retourne l'ensemble des colonnes d'une table existante."""
    try:
        cursor = sqlite_db.execute_sql(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cursor.fetchall()}
    except Exception:
        return set()


def _get_table_unique_indexes(sqlite_db: SqliteDatabase, table_name: str) -> set[tuple[str, ...] | str]:
    """Retourne les index uniques d'une table SQLite."""
    unique_indexes = set()
    try:
        cursor = sqlite_db.execute_sql(f"PRAGMA index_list({table_name})")
        for _, index_name, unique, *_ in cursor.fetchall():
            if not unique:
                continue
            cols = tuple(
                row[2]
                for row in sqlite_db.execute_sql(f"PRAGMA index_info({index_name})").fetchall()
            )
            unique_indexes.add(cols if len(cols) > 1 else cols[0])
    except Exception:
        pass
    return unique_indexes


def _get_model_unique_indexes(model_class) -> set[tuple[str, ...] | str]:
    """Retourne les index uniques attendus par le modèle Peewee."""
    unique_fields: set[tuple[str, ...] | str] = set()
    for field_name, field in model_class._meta.fields.items():
        if getattr(field, "unique", False):
            unique_fields.add(field_name)
    for index, unique in getattr(model_class._meta, "indexes", []) or []:
        if unique:
            unique_fields.add(tuple(index) if len(index) > 1 else index[0])
    return unique_fields


def _migrate_if_needed(sqlite_db: SqliteDatabase, model_class) -> None:
    """Recrée la table si son schéma ou ses index uniques ne correspondent plus au modèle actuel."""
    table_name = model_class._meta.table_name
    existing_cols = _get_table_columns(sqlite_db, table_name)
    if not existing_cols:
        return  # table inexistante — sera créée par create_tables

    expected_cols = set(model_class._meta.columns.keys())
    if existing_cols != expected_cols:
        sqlite_db.drop_tables([model_class], safe=True)
        return

    expected_unique_indexes = _get_model_unique_indexes(model_class)
    existing_unique_indexes = _get_table_unique_indexes(sqlite_db, table_name)
    if existing_unique_indexes != expected_unique_indexes:
        sqlite_db.drop_tables([model_class], safe=True)


def init_db(
    db_path: Path | str = DB_PATH,
    extra_models: list = None,
) -> SqliteDatabase:
    """Initialise la base de données SQLite et crée les tables.

    Args:
        db_path: Chemin vers le fichier SQLite (défaut: setin_data.db).
        extra_models: Liste de modèles supplémentaires à initialiser.

    Returns:
        Instance de la base de données configurée.

    Example:
        >>> from db.models import SetinOrder, SetinProduct
        >>> init_db(extra_models=[SetinOrder, SetinProduct])
    """
    if isinstance(db_path, str):
        db_path = Path(db_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)

    sqlite_db = SqliteDatabase(str(db_path))

    # Lier tous les modèles à cette instance de DB
    for model_class in [SetinOrder, SetinProduct, SetinTracking]:
        model_class._meta.database = sqlite_db

    if extra_models:
        for model_class in extra_models:
            model_class._meta.database = sqlite_db

    # Supprimer les tables dont le schéma est obsolète avant de les recréer
    all_models = list({SetinOrder, SetinProduct, SetinTracking, *(extra_models or [])})
    for model_class in all_models:
        _migrate_if_needed(sqlite_db, model_class)

    sqlite_db.create_tables(all_models, safe=True)

    return sqlite_db
