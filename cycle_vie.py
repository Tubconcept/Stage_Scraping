"""
Cycle de vie des articles — application des verdicts vérifiés.

Troisième et dernier temps de la chaîne, **le seul qui écrit** :

    audit_catalogue.py    245 865 lignes → 2 388 candidats   (lecture seule)
    verifier_retraits.py  2 388 candidats → verdicts HTTP    (lecture seule)
    cycle_vie.py          verdicts → état en base            ← ICI

⚠️ **Aucune suppression, jamais.** Une fiche retirée garde sa ligne. On écrit :

    etat_fournisseur     « retire »
    retire_le            l'horodatage
    verifie_le           quand on l'a constaté
    product_stock_status « RETIRE DU CATALOGUE »

Le libellé de stock est **explicite à dessein** : « PAS EN STOCK » signifierait
« en rupture », pas « n'existe plus ». Le mapping texte → quantité du PIM doit le
faire tomber à zéro.

C'est **réversible** : un article qui réapparaît au catalogue repasse ``actif``
dès le prochain passage du scrape (``marquer_vue``), et un verdict « publie » ou
« renomme » le réactive immédiatement.

Ce que ce script ne fait PAS
---------------------------
Il ne décide rien. Il applique des verdicts déjà **vérifiés en HTTP**, un par
URL. Il n'y a pas de balayage « toutes les fiches non vues depuis X jours » —
mesuré le 07/08/2026, cela marquerait retirés 79 % d'articles vivants chez Sider,
dont le sitemap est trop incomplet pour conclure à une absence.

Utilisation
-----------
    python cycle_vie.py                     # simulation, tous les verdicts
    python cycle_vie.py --site sider
    python cycle_vie.py --apply             # écrit réellement
    python cycle_vie.py --etat              # état actuel, sans rien lire d'autre
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from core.config import CSV_DIR, DIRECTORY
from db.mariadb_db import (
    COL_ETAT,
    COL_RETIRE_LE,
    COL_VUE,
    ETAT_RETIRE,
    SITE_PREFIX,
    _get_conn,
    _table,
    appliquer_verdicts,
    dernier_run_complet,
    init_site_db,
)

DOSSIER = CSV_DIR / "audit_cycle_vie"

#: Verdicts qui confirment un retrait (cf. verifier_retraits.py).
VERDICTS_RETRAIT = ("retire", "fin_de_vie", "redirige_ailleurs")


def lire_verdicts(site: str) -> dict[str, str]:
    """``{url: verdict}`` depuis le CSV produit par la vérification."""
    chemin = DOSSIER / f"verdicts_{site}.csv"
    if not chemin.exists():
        return {}
    with open(chemin, encoding="utf-8") as fichier:
        return {
            ligne["url"]: ligne["verdict"]
            for ligne in csv.DictReader(fichier, delimiter=";")
            if ligne.get("url")
        }


def etat_actuel(site: str) -> dict:
    """Photo du cycle de vie en base pour ce fournisseur. **Lecture seule**."""
    table = _table(site, "products")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (COL_ETAT,))
            if cur.fetchone() is None:
                return {"site": site, "prete": False}
            cur.execute(
                f"SELECT COALESCE(NULLIF(`{COL_ETAT}`, ''), '(non renseigné)'), COUNT(*) "
                f"FROM `{table}` GROUP BY 1 ORDER BY 2 DESC"
            )
            etats = dict(cur.fetchall())
            cur.execute(f"SELECT COUNT(*) FROM `{table}` WHERE `{COL_VUE}` IS NOT NULL")
            vues = cur.fetchone()[0]
            cur.execute(f"SELECT MAX(`{COL_VUE}`), MAX(`{COL_RETIRE_LE}`) FROM `{table}`")
            derniere_vue, dernier_retrait = cur.fetchone()
            return {"site": site, "prete": True, "etats": etats, "vues": vues,
                    "derniere_vue": derniere_vue, "dernier_retrait": dernier_retrait}
    finally:
        conn.close()


def _afficher_etat(photo: dict) -> None:
    site = photo["site"]
    if not photo["prete"]:
        print(f"\n═══ {site} — colonnes de cycle de vie absentes "
              f"(lancer un scrape ou `python cycle_vie.py --apply`)")
        return
    print(f"\n═══ {site}")
    for etat, nombre in photo["etats"].items():
        print(f"    {etat:20} {nombre:>7}")
    print(f"    {'avec date de vue':20} {photo['vues']:>7}")
    print(f"    dernière vue : {photo['derniere_vue']}  |  "
          f"dernier retrait : {photo['dernier_retrait']}")
    run = dernier_run_complet(site)
    print(f"    dernier run complet : {run['fin'] if run else '(aucun)'}")


def _afficher_application(rapport: dict, verdicts: dict[str, str]) -> None:
    site = rapport["site"]
    mode = "APPLIQUÉ" if rapport["applique"] else "SIMULATION"
    retraits = sum(1 for v in verdicts.values() if v in VERDICTS_RETRAIT)
    print(f"\n═══ {site} — {mode}")
    print(f"    verdicts lus         : {len(verdicts):>7} "
          f"({retraits} retrait(s) confirmé(s))")
    print(f"    lignes à retirer     : {rapport['retires']:>7}")
    print(f"    lignes à réactiver   : {rapport['reactives']:>7}")
    if rapport["ignores"]:
        print(f"    verdicts sans effet  : {rapport['ignores']:>7} (indéterminés)")
    if rapport["introuvables"]:
        print(f"    ⚠ URL introuvables   : {rapport['introuvables']:>7} "
              f"(verdicts plus à jour que la base ?)")


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        description="Applique en base les verdicts de retrait vérifiés.",
    )
    parseur.add_argument("--site", default="all", choices=[*SITE_PREFIX, "all"])
    parseur.add_argument("--apply", action="store_true",
                         help="écrit réellement (défaut : simulation)")
    parseur.add_argument("--etat", action="store_true",
                         help="affiche seulement l'état actuel, sans rien appliquer")
    args = parseur.parse_args(argv)

    sites = list(SITE_PREFIX) if args.site == "all" else [args.site]

    if args.etat:
        for site in sites:
            try:
                _afficher_etat(etat_actuel(site))
            except Exception as exc:
                print(f"\n═══ {site} — ÉCHEC : {type(exc).__name__}: {exc}")
        return 0

    total_retires = 0
    for site in sites:
        verdicts = lire_verdicts(site)
        if not verdicts:
            print(f"\n═══ {site} — aucun verdict "
                  f"(lancer audit_catalogue.py puis verifier_retraits.py)")
            continue
        try:
            if args.apply:
                # Crée au besoin les colonnes de cycle de vie et le journal des runs.
                init_site_db(site)
            rapport = appliquer_verdicts(site, verdicts, apply=args.apply)
        except Exception as exc:
            print(f"\n═══ {site} — ÉCHEC : {type(exc).__name__}: {exc}")
            continue
        _afficher_application(rapport, verdicts)
        total_retires += rapport["retires"]

    print(f"\n─── {total_retires} ligne(s) passée(s) en « {ETAT_RETIRE} »")
    if not args.apply:
        print("    (simulation — relancer avec --apply pour écrire en base)")
    print("    Aucune ligne n'est SUPPRIMÉE : l'état est réversible.")
    return 0


if __name__ == "__main__":
    if str(DIRECTORY) not in sys.path:
        sys.path.insert(0, str(DIRECTORY))
    sys.exit(main())
