"""Lecture et validation de fichiers de références produits (CSV / JSON).

API publique
------------
load_refs(file_path)             → list[str]                 # lecture brute
validate_refs(refs, site)        → (valid: list, invalid: list)
copy_import_file(src)            → Path                      # copie dans data/imports/
site_ref_label(site)             → str                       # description format attendu
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent.parent

IMPORT_CSV_DIR  = DIRECTORY / "data" / "imports" / "csv"
IMPORT_JSON_DIR = DIRECTORY / "data" / "imports" / "json"

# Validation par fournisseur
# - legallais (P1) : 6 chiffres   ex: 196874
# - prolians  (P3) : 8 chiffres   ex: 46741468
# - setin     (P5) : 6 alphanumériques (maj insensible)  ex: EFF547
_PATTERNS: dict[str, re.Pattern] = {
    "legallais": re.compile(r"^\d{6}$"),
    "prolians":  re.compile(r"^\d{8}$"),
    "setin":     re.compile(r"^[A-Za-z0-9]{6}$"),
}

_SITE_LABELS: dict[str, str] = {
    "legallais": "Legallais — 6 chiffres (ex : 196874)",
    "prolians":  "Prolians — 8 chiffres (ex : 46741468)",
    "setin":     "Setin — 6 caractères alphanumériques (ex : EFF547)",
}

_HEADER_WORDS = frozenset({
    "ref", "reference", "références", "refs",
    "reference_fournisseur", "ref_fournisseur", "article",
})

# Caractères invisibles qui cassent le parser JSON (espace insécable, zero-width, BOM résiduel...)
_INVISIBLE_CHARS = (
    " ", "​", "‌", "‍", "﻿", " ", " ",
)

_JSON_LIST_KEYS = ("refs", "references", "data", "produits", "articles")


def _read_text_any_encoding(file_path: Path) -> str:
    """Lit un fichier texte en essayant plusieurs encodages courants."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(file_path, "r", encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
    return file_path.read_bytes().decode("latin-1")


def _normalize_json_text(raw: str) -> str:
    """Supprime les caractères invisibles et normalise les guillemets typographiques."""
    cleaned = raw
    for ch in _INVISIBLE_CHARS:
        cleaned = cleaned.replace(ch, " ")
    return (
        cleaned
        .replace("“", '"').replace("”", '"')
        .replace("‘", "'").replace("’", "'")
    )


def _extract_refs_from_json_data(data) -> list[str]:
    """Extrait la liste de références d'un JSON déjà décodé (liste ou objet)."""
    if isinstance(data, list):
        return [str(r).strip() for r in data if str(r).strip()]
    if isinstance(data, dict):
        for key in _JSON_LIST_KEYS:
            if key in data and isinstance(data[key], list):
                return [str(r).strip() for r in data[key] if str(r).strip()]
    raise ValueError(
        "Format JSON invalide.\n"
        'Attendu : une liste ["ref1", "ref2", ...] '
        'ou un objet {"refs": ["ref1", ...]}'
    )


def _load_json_refs(file_path: Path) -> list[str]:
    """Lit un fichier JSON et retourne la liste brute des références."""
    raw = _read_text_any_encoding(file_path)
    cleaned = _normalize_json_text(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Le fichier « {file_path.name} » n'est pas du JSON valide.\n\n"
            f"Erreur : {exc.msg} (ligne {exc.lineno}, colonne {exc.colno})\n\n"
            "Vérifiez que le contenu respecte le format :\n"
            '  • Liste :  ["TSA211", "ECO044", ...]\n'
            '  • Objet :  {"refs": ["TSA211", ...]}\n\n'
            "Ou utilisez directement un fichier .csv"
        ) from exc

    return _extract_refs_from_json_data(data)


def _sniff_csv_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;|\t")
    except csv.Error:
        return csv.excel


def _load_csv_refs(file_path: Path) -> list[str]:
    """Lit un fichier CSV et retourne la liste brute des références."""
    refs: list[str] = []
    with open(file_path, "r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        dialect = _sniff_csv_dialect(sample)
        for row in csv.reader(fh, dialect):
            for cell in row:
                val = cell.strip()
                if val and val.lower() not in _HEADER_WORDS:
                    refs.append(val)
    return refs


def load_refs(file_path: Path) -> list[str]:
    """Lit un fichier CSV ou JSON et retourne la liste brute des références."""
    suffix = file_path.suffix.lower()

    if suffix == ".json":
        return _load_json_refs(file_path)
    if suffix == ".csv":
        return _load_csv_refs(file_path)

    raise ValueError(
        f"Format de fichier non supporté : {suffix!r}.\n"
        "Utilisez un fichier .csv ou .json"
    )


def validate_refs(refs: list[str], site: str) -> tuple[list[str], list[str]]:
    """Valide les références selon le format du fournisseur.

    Returns:
        (valid_refs, invalid_refs)

    Raises:
        ValueError si le site est inconnu.
    """
    pattern = _PATTERNS.get(site.lower())
    if pattern is None:
        raise ValueError(
            f"Fournisseur inconnu : {site!r}. "
            f"Valeurs acceptées : {list(_PATTERNS)}"
        )
    valid   = [r for r in refs if pattern.match(r)]
    invalid = [r for r in refs if not pattern.match(r)]
    return valid, invalid


def site_ref_label(site: str) -> str:
    """Retourne la description du format de référence attendu pour un fournisseur."""
    return _SITE_LABELS.get(site.lower(), site)


def copy_import_file(src: Path) -> Path:
    """Copie le fichier dans data/imports/csv/ ou json/.

    Si un fichier identique (même contenu) existe déjà, retourne son chemin
    sans recréer. Si le contenu diffère, ajoute un timestamp au nom.
    """
    dest_dir = IMPORT_JSON_DIR if src.suffix.lower() == ".json" else IMPORT_CSV_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    if dest.exists():
        src_hash = hashlib.sha512(src.read_bytes()).hexdigest()
        dst_hash = hashlib.sha512(dest.read_bytes()).hexdigest()
        if src_hash == dst_hash:
            return dest
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = dest_dir / f"{src.stem}_{ts}{src.suffix}"

    shutil.copy2(src, dest)
    return dest
