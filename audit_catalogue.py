"""
Audit catalogue fournisseur ↔ base — **lecture seule**, aucune écriture.

Répond à une seule question : *combien d'articles enregistrés en base ne sont
plus publiés par le fournisseur ?* C'est le chiffrage préalable au suivi du
cycle de vie (marquage « dernière vue », machine à états, mise à 0 du stock).

Ce script **ne conclut rien** et ne modifie rien : il produit des **candidats**.
Une URL absente de l'énumération n'est qu'un signal faible — les sitemaps
tronquent, les sessions meurent, les fiches « gamme » ne s'y publient pas. Le
verdict demande une vérification ciblée (404/410, mention « fin de vie »), qui
n'a de sens qu'une fois le volume connu.

Comparer ce qui est comparable
------------------------------
Le sitemap énumère des **pages** ; la base contient parfois une ligne **par
variante**. Chaque fournisseur a donc sa clé de page :

Les clés vivent dans ``core/cle_page.py``, avec le pourquoi de chacune. En deux
mots : **le slug n'identifie rien** — Legallais n'en publie pas le même au
sitemap que dans ses URL, et Setin renomme ses fiches en place. Seuls les
identifiants (``aNNNNN`` chez Setin, id de gamme chez Legallais) sont stables.

  Sonepar   PAS d'énumération disponible (aucun module sitemap) → non audité.

Utilisation
-----------
    python audit_catalogue.py                     # les quatre fournisseurs
    python audit_catalogue.py --site sider
    python audit_catalogue.py --out orphelins/    # écrit la liste des candidats
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

from core.cle_page import cle_page
from core.config import DIRECTORY
from db.mariadb_db import SITE_PREFIX, _get_conn

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CLÉS DE PAGE (pures)
# ═══════════════════════════════════════════════════════════════════════════════

def _enumerer_prolians():
    from scrapers.Prolians_P3.products import prolians_sitemap
    return (e["url"] for e in prolians_sitemap.iter_product_entries(logger=_log))


def _enumerer_setin():
    from scrapers.Setin_P5.products import setin_sitemap
    return (e["url"] for e in setin_sitemap.iter_entrees_produit())


def _enumerer_legallais():
    from scrapers.Legallais_P1.products import legallais_sitemap
    return (e["url"] for e in legallais_sitemap.iter_entrees_produit())


def _enumerer_sider():
    from scrapers.Sider_P6.products import sider_sitemap
    return (e["url"] for e in sider_sitemap.iter_product_entries(logger=_log))


#: Par fournisseur : comment énumérer, comment ramener une URL à sa page, et la
#: réserve à afficher avec le résultat.
SOURCES: dict[str, dict] = {
    "prolians": {"enumerer": _enumerer_prolians, "reserve": ""},
    "sider": {"enumerer": _enumerer_sider, "reserve": ""},
    "setin": {
        "enumerer": _enumerer_setin,
        "reserve": "clé = id de fiche « aNNNNN » (le slug est renommé) ; "
                   "1 page = N variantes.",
    },
    "legallais": {
        "enumerer": _enumerer_legallais,
        "reserve": "comparaison au niveau GAMME (id numérique) ; 1 gamme = N articles.",
    },
    # Sonepar : aucun module d'énumération. L'ajouter demanderait de découvrir un
    # sitemap ou de parcourir l'arbre de catégories — hors périmètre d'un audit
    # en lecture seule et bon marché.
    "sonepar": {"enumerer": None, "reserve": "aucune énumération disponible"},
}


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def lignes_en_base(site: str) -> list[tuple[int, str]]:
    """``(id, url)`` de toutes les lignes du fournisseur. **Lecture seule**."""
    table = f"{SITE_PREFIX[site]}_products"
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT `id`, `product_fournisseur_url` FROM `{table}` "
                f"WHERE `product_fournisseur_url` <> ''"
            )
            return [(row[0], row[1]) for row in cur.fetchall()]
    finally:
        conn.close()


def auditer(site: str) -> dict:
    """Compare l'énumération du fournisseur au contenu de la base.

    Returns:
        Rapport : compteurs par page ET par ligne, plus les URL orphelines.
    """
    source = SOURCES.get(site)
    if source is None or source["enumerer"] is None:
        return {"site": site, "auditable": False,
                "reserve": (source or {}).get("reserve", "fournisseur inconnu")}

    def cle(url: str) -> str:
        return cle_page(site, url)
    lignes = lignes_en_base(site)
    # On garde les URL, pas seulement les id : ce sont elles qu'il faudra
    # re-fetcher à l'étape de vérification. Pour Legallais la clé est un simple
    # identifiant de gamme, qui ne mène à rien tout seul.
    pages_base: dict[str, list[str]] = defaultdict(list)
    sans_cle = 0
    for _row_id, url in lignes:
        valeur = cle(url)
        if not valeur:
            # URL inexploitable (ni id, ni chemin) : ce n'est pas une orpheline,
            # c'est une ligne à corriger — on la compte à part.
            sans_cle += 1
            continue
        pages_base[valeur].append(url)

    pages_site = {cle(u) for u in source["enumerer"]() if u}
    pages_site.discard("")

    cles_orphelines = sorted(set(pages_base) - pages_site)
    manquantes = sorted(pages_site - set(pages_base))
    urls_orphelines = [u for c in cles_orphelines for u in pages_base[c]]

    return {
        "site": site,
        "auditable": True,
        "reserve": source["reserve"],
        "lignes_base": len(lignes),
        "pages_base": len(pages_base),
        "pages_site": len(pages_site),
        "pages_orphelines": len(cles_orphelines),
        "lignes_orphelines": len(urls_orphelines),
        "pages_manquantes": len(manquantes),
        "sans_cle": sans_cle,
        "couverture_pct": (
            round(100 * len(pages_site & set(pages_base)) / len(pages_site), 1)
            if pages_site else 0.0
        ),
        "orphelines": urls_orphelines,
        "manquantes": manquantes,
    }


def _afficher(rapport: dict) -> None:
    site = rapport["site"]
    if not rapport["auditable"]:
        print(f"\n═══ {site} — NON AUDITÉ : {rapport['reserve']}")
        return

    part = (100 * rapport["lignes_orphelines"] / rapport["lignes_base"]
            if rapport["lignes_base"] else 0.0)
    print(f"\n═══ {site} ({SITE_PREFIX[site]})")
    print(f"    catalogue publié     : {rapport['pages_site']:>7} pages")
    print(f"    en base              : {rapport['lignes_base']:>7} lignes "
          f"({rapport['pages_base']} pages)")
    print(f"    couverture           : {rapport['couverture_pct']:>7} %")
    print(f"    À SCRAPER (manquant) : {rapport['pages_manquantes']:>7} pages")
    print(f"    CANDIDATS AU RETRAIT : {rapport['pages_orphelines']:>7} pages "
          f"→ {rapport['lignes_orphelines']} lignes ({part:.1f} % de la base)")
    if rapport["sans_cle"]:
        print(f"    ⚠ sans clé exploitable: {rapport['sans_cle']:>6} lignes "
              f"(URL à corriger, PAS des orphelines)")
    if rapport["reserve"]:
        print(f"    réserve              : {rapport['reserve']}")
    for url in rapport["orphelines"][:3]:
        print(f"      · {url[:96]}")


def _ecrire(rapport: dict, dossier: Path) -> None:
    """Dépose la liste des candidats, matière première de la vérification ciblée."""
    if not rapport["auditable"] or not rapport["orphelines"]:
        return
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"orphelins_{rapport['site']}.txt"
    chemin.write_text("\n".join(rapport["orphelines"]) + "\n", encoding="utf-8")
    print(f"    → {len(rapport['orphelines'])} URL écrites dans {chemin}")


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        description="Audit lecture seule : articles en base absents du catalogue fournisseur.",
    )
    parseur.add_argument("--site", default="all", choices=[*SITE_PREFIX, "all"])
    parseur.add_argument("--out", default="",
                         help="dossier où écrire la liste des URL orphelines")
    args = parseur.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    sites = list(SITE_PREFIX) if args.site == "all" else [args.site]

    total_lignes = total_orphelines = 0
    for site in sites:
        try:
            rapport = auditer(site)
        except Exception as exc:
            print(f"\n═══ {site} — ÉCHEC : {type(exc).__name__}: {exc}")
            continue
        _afficher(rapport)
        if args.out:
            _ecrire(rapport, Path(args.out))
        if rapport["auditable"]:
            total_lignes += rapport["lignes_base"]
            total_orphelines += rapport["lignes_orphelines"]

    print(f"\n─── Total audité : {total_orphelines} ligne(s) candidates au retrait "
          f"sur {total_lignes} ({100 * total_orphelines / total_lignes:.1f} %)"
          if total_lignes else "\n─── Rien d'audité.")
    print("    Aucune écriture n'a été faite : ce sont des CANDIDATS, pas un verdict.")
    return 0


if __name__ == "__main__":
    if str(DIRECTORY) not in sys.path:
        sys.path.insert(0, str(DIRECTORY))
    sys.exit(main())
