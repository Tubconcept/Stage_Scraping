"""
État des sessions fournisseurs — présence, âge, validité.

Une session est un ``storage_state`` Playwright (cookies + localStorage) obtenu
par login, stocké dans ``playwright_profiles/<site>/session.json`` — **le chemin
historique du projet**, pour que les sessions déjà ouvertes par les scrapers
existants restent utilisables par les nouvelles méthodes.

À côté, un ``meta.json`` porte le statut et la date de dernière connexion.

⚠️ **L'âge compte.** Un ``statut: ok`` ne passe à « expirée » que si quelqu'un le
pose explicitement — or personne ne le fait quand le site coupe la session de son
côté. Vécu sur SCRAPPER_App : un meta « ok » sur une session vieille de 16 jours,
et trois runs entiers tournant session morte (79 947 lignes pour 0,4 % de prix).
Au-delà de ``AGE_MAX_JOURS``, la session est donc tenue pour morte : mieux vaut
un login de trop qu'un catalogue scrapé sans prix.

Module **pur** (système de fichiers seulement) — testable sans navigateur. Le
login lui-même vit dans ``core/login_auto.py``.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from core.config import PROFILES_DIR

STATUT_OK = "ok"
STATUT_ABSENTE = "absente"
STATUT_EXPIREE = "expiree"

#: Au-delà de cet âge, une session est tenue pour morte même si sa méta dit « ok ».
#: 3 jours : les sessions B2B observées (Setin, Prolians, Sider) tiennent quelques
#: jours au mieux.
AGE_MAX_JOURS = 3.0

#: Nom du fichier de session — celui déjà utilisé par les scrapers historiques.
NOM_SESSION = "session.json"
NOM_META = "meta.json"


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat()


class GestionnaireSessions:
    """Lecture/écriture de l'état des sessions sous ``playwright_profiles/``."""

    def __init__(self, dossier: Path | None = None) -> None:
        self._dossier = Path(dossier) if dossier else PROFILES_DIR

    def _dossier_fournisseur(self, fournisseur: str) -> Path:
        return self._dossier / fournisseur

    def chemin_session(self, fournisseur: str) -> Path:
        """Chemin du ``storage_state`` du fournisseur (créé au besoin par le login)."""
        return self._dossier_fournisseur(fournisseur) / NOM_SESSION

    def _chemin_meta(self, fournisseur: str) -> Path:
        return self._dossier_fournisseur(fournisseur) / NOM_META

    def _lire_meta(self, fournisseur: str) -> dict:
        chemin = self._chemin_meta(fournisseur)
        if not chemin.exists():
            return {}
        try:
            return json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _ecrire_meta(self, fournisseur: str, meta: dict) -> None:
        self._dossier_fournisseur(fournisseur).mkdir(parents=True, exist_ok=True)
        self._chemin_meta(fournisseur).write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )

    # ─── Lecture ─────────────────────────────────────────────────────────────

    def etat(self, fournisseur: str) -> str:
        """``ok`` si présente, non expirée et pas trop vieille ; sinon ``expiree``/``absente``."""
        if not self.chemin_session(fournisseur).exists():
            return STATUT_ABSENTE
        if self._lire_meta(fournisseur).get("statut") == STATUT_EXPIREE:
            return STATUT_EXPIREE
        age = self.age_jours(fournisseur)
        if age is not None and age > AGE_MAX_JOURS:
            return STATUT_EXPIREE
        return STATUT_OK

    def age_jours(self, fournisseur: str) -> float | None:
        """Âge de la session en jours, ou ``None`` si la date est absente/illisible.

        ``None`` plutôt que « expirée » : on ne présume pas la mort d'une session
        sur une méta mal formée — une session peut être bonne sans être horodatée.
        """
        brut = self._lire_meta(fournisseur).get("derniere_connexion")
        if not isinstance(brut, str) or not brut:
            return None
        try:
            quand = datetime.fromisoformat(brut)
        except ValueError:
            return None
        if quand.tzinfo is None:
            quand = quand.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - quand).total_seconds() / 86400

    def derniere_connexion(self, fournisseur: str) -> str | None:
        return self._lire_meta(fournisseur).get("derniere_connexion")

    def sessions_valides(self) -> set[str]:
        """Fournisseurs dont la session est ``ok``."""
        if not self._dossier.exists():
            return set()
        return {
            sous.name
            for sous in self._dossier.iterdir()
            if sous.is_dir() and self.etat(sous.name) == STATUT_OK
        }

    # ─── Écriture ────────────────────────────────────────────────────────────

    def marquer_connecte(self, fournisseur: str) -> None:
        """À appeler **après** la sauvegarde du ``storage_state``.

        Sans cet horodatage, un re-login réussi reste invisible : la session est
        bien renouvelée mais la méta reste périmée, et ``etat()`` annonce
        « expirée » sur une session toute fraîche.
        """
        self._ecrire_meta(
            fournisseur, {"statut": STATUT_OK, "derniere_connexion": _maintenant()}
        )

    def marquer_expiree(self, fournisseur: str) -> None:
        """Marque la session expirée (ex. un scrape a constaté la déconnexion)."""
        meta = self._lire_meta(fournisseur)
        meta["statut"] = STATUT_EXPIREE
        self._ecrire_meta(fournisseur, meta)

    def supprimer(self, fournisseur: str) -> None:
        """Supprime session + méta d'un fournisseur."""
        dossier = self._dossier_fournisseur(fournisseur)
        if dossier.exists():
            shutil.rmtree(dossier, ignore_errors=True)
