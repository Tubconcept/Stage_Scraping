"""
Dédoublonnage des fiches produit déjà en base (MariaDB).

Les nouveaux scrapes ne créent plus de doublon : ``db.mariadb_db.save_product``
identifie chaque fiche par son ``product_uid`` (cf. core/dedup.py), et une
passe de dédoublonnage tourne automatiquement à la fin de chaque scrape
produits lancé depuis la GUI.

Ce script sert à ce que l'automatisme ne fait pas : régler le passif hérité
des anciens scrapes, auditer une autre clé d'identité (``--criteres ref``),
ou forcer la fusion des cas ambigus (``--souple``) après relecture.

Utilisation
-----------
    python dedoublonnage.py                      # simulation, tous les sites
    python dedoublonnage.py --site sider         # simulation, un seul site
    python dedoublonnage.py --apply              # applique réellement
    python dedoublonnage.py --site sonepar --criteres ref --apply
    python dedoublonnage.py --site prolians --criteres ref --souple

Options
-------
--apply       écrit en base (sans ce drapeau : simulation, aucune écriture)
--criteres    force les critères d'identité (url, ref, ean ; séparés par une
              virgule) au lieu de ceux configurés dans core/dedup.py
--souple      désactive le garde-fou : fusionne même des lignes qui divergent
              (désignation, EAN ou prix différents). À n'utiliser qu'après
              avoir inspecté le rapport de conflits.
"""

from __future__ import annotations

import argparse
import sys

from core.logger import logger
from db.mariadb_db import SITE_PREFIX, deduplicate_products, init_site_db


def _afficher(rapport: dict) -> None:
    """Affiche le rapport d'un site sous forme lisible."""
    mode = "APPLIQUÉ" if rapport["applique"] else "SIMULATION"
    print(f"\n═══ {rapport['site']} ({rapport['table']}) — {mode}")
    print(f"    critères d'identité   : {', '.join(rapport['criteres'])}")
    print(f"    lignes en base        : {rapport['lignes']}")
    print(f"    groupes de doublons   : {rapport['groupes_doublons']}")
    print(f"    lignes supprimées     : {rapport['lignes_supprimees']}")
    print(f"    conflits non fusionnés: {rapport['conflits']}")
    if rapport["lignes"]:
        restant = rapport["lignes"] - rapport["lignes_supprimees"]
        print(f"    → {restant} fiches uniques")
    for exemple in rapport["exemples"][:5]:
        if exemple["type"] == "fusion":
            champs = ", ".join(exemple["champs_recuperes"]) or "(rien à récupérer)"
            print(f"      · fusion   id={exemple['garde']} ← {exemple['supprime']}"
                  f" | champs récupérés : {champs}")
        else:
            print(f"      · CONFLIT  ids={exemple['ids']} | {exemple['url'][:80]}")


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        description="Fusionne les fiches produit en double dans MariaDB.",
    )
    parseur.add_argument("--site", default="all",
                         choices=[*SITE_PREFIX, "all"],
                         help="fournisseur à traiter (défaut : tous)")
    parseur.add_argument("--apply", action="store_true",
                         help="écrit réellement en base (défaut : simulation)")
    parseur.add_argument("--criteres", default="",
                         help="critères d'identité forcés, ex. « ref » ou « ref,url »")
    parseur.add_argument("--souple", action="store_true",
                         help="désactive le garde-fou de ressemblance")
    args = parseur.parse_args(argv)

    sites = list(SITE_PREFIX) if args.site == "all" else [args.site]
    criteres = tuple(c.strip() for c in args.criteres.split(",") if c.strip()) or None

    total_supprime = 0
    total_conflits = 0
    for site in sites:
        try:
            if args.apply:
                # Crée au besoin la colonne product_uid et son index UNIQUE ;
                # la simulation, elle, reste en lecture seule.
                init_site_db(site)
            rapport = deduplicate_products(
                site, apply=args.apply, strict=not args.souple, criteres=criteres,
            )
        except Exception as exc:  # table absente, base injoignable…
            logger.error("Dédoublonnage %s impossible : %s", site, exc)
            print(f"\n═══ {site} — ÉCHEC : {exc}")
            continue
        _afficher(rapport)
        total_supprime += rapport["lignes_supprimees"]
        total_conflits += rapport["conflits"]

    print(f"\n─── Total : {total_supprime} ligne(s) en double, "
          f"{total_conflits} conflit(s) laissé(s) en l'état")
    if not args.apply:
        print("    (simulation — relancer avec --apply pour écrire en base)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
