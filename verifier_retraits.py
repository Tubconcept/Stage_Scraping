"""
Vérification ciblée des candidats au retrait — **lecture seule**, aucune écriture.

Deuxième temps du chiffrage du cycle de vie. ``audit_catalogue.py`` réduit
245 865 lignes à ~2 400 URL suspectes ; ce script va **demander au site** ce
qu'il en est, une par une.

Pourquoi cette étape n'est pas facultative
------------------------------------------
L'absence du sitemap est un signal **faible**, et très inégal selon le
fournisseur. Sondage du 07/08/2026 sur 30 candidats chacun :

    sider      83 % de faux positifs (la page répond 200)
    prolians   30 %
    setin      20 %

Chez Sider, quatre candidats sur cinq sont toujours en ligne : un balayage
fondé sur le seul diff y marquerait « retiré » l'essentiel du catalogue. C'est
la vérification qui tranche, pas l'énumération.

Quatre verdicts, dont un qui ne conclut rien
--------------------------------------------
    retire        404 / 410 — la page a disparu
    fin_de_vie    200 mais la page l'annonce (« n'est plus disponible »…)
    publie        200 sans rien de tel → faux positif du diff
    indetermine   403 / 429 / 5xx / réseau → ON NE CONCLUT PAS

``indetermine`` est le verdict le plus important : un antibot qui renvoie 403 ne
dit pas qu'un article est retiré. Le confondre avec un retrait ferait passer à
zéro le stock d'articles vivants — l'erreur exactement inverse de celle qu'on
cherche à corriger.

⚠️ La vérification se fait **sans session**, sur la vue publique. C'est
volontaire : les sessions meurent en cours de route et une session morte
transformerait tout le catalogue en « retiré ». La contrepartie est qu'un
article visible seulement une fois connecté sera vu comme absent — d'où le
verdict, jamais l'écriture automatique.

Utilisation
-----------
    python verifier_retraits.py                    # tous les fichiers d'orphelins
    python verifier_retraits.py --site sider
    python verifier_retraits.py --site sider --limit 50
"""

from __future__ import annotations

import argparse
import csv
import gzip
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.cle_page import cle_page, est_categorie_setin
from core.config import CSV_DIR, DIRECTORY
from core.polite_http import build_browser_headers

#: Où l'audit dépose ses candidats, et où l'on écrit les verdicts.
DOSSIER = CSV_DIR / "audit_cycle_vie"

#: Requêtes simultanées. Bas volontairement : ce sont des sites de production,
#: certains derrière un antibot. 2 400 URL à 4 en parallèle ≈ 15 min.
CONCURRENCE = 4

#: Attente aléatoire avant chaque requête (désynchronise les workers).
JITTER = (0.3, 0.9)

TIMEOUT = 25
REESSAIS = 2

#: Statuts qui méritent un réessai temporisé plutôt qu'un verdict.
STATUTS_REESSAI = frozenset({403, 429, 503})

#: Formulations qui annoncent explicitement la fin de commercialisation. Volontairement
#: peu nombreuses et sans ambiguïté : un marqueur trop large ferait passer pour morts
#: des articles vivants, et cette erreur-là se paie en stock mis à zéro à tort.
MARQUEURS_FIN_DE_VIE = (
    "n'est plus disponible",
    "n'est plus commercialisé",
    "produit non disponible",
    "produit indisponible définitivement",
    "fin de commercialisation",
    "article supprimé",
    "page introuvable",
    "cette page n'existe plus",
)

VERDICT_RETIRE = "retire"
VERDICT_FIN_DE_VIE = "fin_de_vie"
VERDICT_REDIRIGE_AILLEURS = "redirige_ailleurs"  # repli vers une rubrique → retrait déguisé
VERDICT_RENOMME = "renomme"                      # même fiche, nouveau slug → VIVANT
VERDICT_PUBLIE = "publie"
VERDICT_INDETERMINE = "indetermine"

#: Verdicts qui valent confirmation d'un retrait.
VERDICTS_RETRAIT = (VERDICT_RETIRE, VERDICT_FIN_DE_VIE, VERDICT_REDIRIGE_AILLEURS)


def _recuperer(url: str) -> tuple[int | str, str, str]:
    """GET poli conservant le **code HTTP** et l'**URL finale**.

    Retourne ``(statut, url_finale, corps)``.

    ``core.polite_http.polite_get`` ne rend que le corps : il confond 404 et 403,
    c'est-à-dire « retiré » et « bloqué ».

    ⚠️ ``urlopen`` suit les redirections **en silence**. Sans l'URL finale, une
    fiche retirée redirigée vers sa rubrique compte comme un 200 vivant — mesuré
    sur Setin, où 6 « encore publiés » sur 6 étaient en réalité des redirections,
    les unes vers la fiche renommée (article vivant), les autres vers une
    catégorie (retrait déguisé). Le statut seul ne permet pas de trancher ; la
    CIBLE, si.
    """
    entetes = build_browser_headers()
    for essai in range(REESSAIS + 1):
        time.sleep(random.uniform(*JITTER))  # noqa: S311 — jitter, pas de la crypto
        try:
            requete = urllib.request.Request(url, headers=entetes)
            with urllib.request.urlopen(requete, timeout=TIMEOUT) as reponse:  # noqa: S310
                brut = reponse.read(500_000)
                if (reponse.headers.get("Content-Encoding") or "").lower() == "gzip" \
                        or brut[:2] == b"\x1f\x8b":
                    brut = gzip.decompress(brut)
                return reponse.status, reponse.geturl(), brut.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in STATUTS_REESSAI and essai < REESSAIS:
                time.sleep(2.0**essai + random.uniform(0.0, 1.5))  # noqa: S311
                continue
            return exc.code, url, ""
        except Exception as exc:
            if essai < REESSAIS:
                time.sleep(1.5**essai + random.uniform(0.0, 0.5))  # noqa: S311
                continue
            return f"ERR {type(exc).__name__}", url, ""
    return "ERR epuise", url, ""


def marqueurs_trouves(corps: str) -> list[str]:
    """Mentions de fin de vie repérées dans la page. **Pur**."""
    bas = (corps or "").lower()
    return [m for m in MARQUEURS_FIN_DE_VIE if m in bas]


def verdict(site: str, url: str, statut: int | str, url_finale: str,
            corps: str) -> tuple[str, str]:
    """``(verdict, indices)`` à partir du statut, de la cible et du corps. **Pur**.

    Tout ce qui n'est pas une disparition franche, une redirection hors fiche ou
    une mention explicite reste ``publie`` ou ``indetermine`` : on ne déduit
    jamais un retrait d'un silence.
    """
    if statut in (404, 410):
        return VERDICT_RETIRE, str(statut)
    if statut != 200:
        return VERDICT_INDETERMINE, str(statut)

    indices = marqueurs_trouves(corps)
    if indices:
        return VERDICT_FIN_DE_VIE, " | ".join(indices)

    if _a_bouge(url, url_finale):
        # Redirection : reste-t-on sur LA MÊME fiche (renommage) ou part-on
        # ailleurs (repli vers une rubrique = retrait déguisé en 200) ?
        if est_categorie_setin(url_finale):
            return VERDICT_REDIRIGE_AILLEURS, f"→ catégorie : {url_finale}"
        if cle_page(site, url) and cle_page(site, url) == cle_page(site, url_finale):
            return VERDICT_RENOMME, f"→ {url_finale}"
        return VERDICT_REDIRIGE_AILLEURS, f"→ {url_finale}"
    return VERDICT_PUBLIE, ""


def _a_bouge(url: str, finale: str) -> bool:
    """Vrai si l'URL finale diffère de la demandée (hors fragment/slash). **Pur**."""
    def _nu(u: str) -> str:
        return (u or "").split("#", 1)[0].rstrip("/")
    return _nu(url) != _nu(finale)


def verifier(site: str, urls: list[str], concurrence: int = CONCURRENCE) -> list[dict]:
    """Vérifie chaque URL en parallèle borné. Retourne une ligne de verdict par URL."""
    def _une(url: str) -> dict:
        statut, finale, corps = _recuperer(url)
        decision, indices = verdict(site, url, statut, finale, corps)
        return {"url": url, "statut": statut, "url_finale": finale,
                "verdict": decision, "indices": indices}

    with ThreadPoolExecutor(max_workers=concurrence) as pool:
        return list(pool.map(_une, urls))


def _resumer(site: str, lignes: list[dict], secondes: float) -> dict:
    compte: dict[str, int] = {}
    for ligne in lignes:
        compte[ligne["verdict"]] = compte.get(ligne["verdict"], 0) + 1
    total = len(lignes) or 1
    confirmes = sum(compte.get(v, 0) for v in VERDICTS_RETRAIT)
    vivants = compte.get(VERDICT_PUBLIE, 0) + compte.get(VERDICT_RENOMME, 0)
    print(f"\n═══ {site} — {len(lignes)} candidats vérifiés en {secondes / 60:.1f} min")
    print(f"    RETIRÉS (404/410)    : {compte.get(VERDICT_RETIRE, 0):>6}")
    print(f"    fin de vie annoncée  : {compte.get(VERDICT_FIN_DE_VIE, 0):>6}")
    print(f"    redirigés ailleurs   : {compte.get(VERDICT_REDIRIGE_AILLEURS, 0):>6} "
          f"(repli vers une rubrique = retrait déguisé)")
    print(f"    RENOMMÉS (vivants)   : {compte.get(VERDICT_RENOMME, 0):>6} "
          f"(même fiche, nouveau slug)")
    print(f"    encore publiés       : {compte.get(VERDICT_PUBLIE, 0):>6}")
    print(f"    indéterminés         : {compte.get(VERDICT_INDETERMINE, 0):>6} "
          f"(bloqué / réseau — AUCUNE conclusion)")
    print(f"    → {confirmes} retrait(s) CONFIRMÉ(S), {vivants} vivant(s) "
          f"({100 * vivants / total:.0f} % de faux positifs du diff)")
    return compte


def _ecrire(site: str, lignes: list[dict]) -> Path:
    chemin = DOSSIER / f"verdicts_{site}.csv"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "w", newline="", encoding="utf-8") as fichier:
        auteur = csv.DictWriter(
            fichier, fieldnames=["url", "statut", "verdict", "url_finale", "indices"],
            delimiter=";", quoting=csv.QUOTE_ALL,
        )
        auteur.writeheader()
        auteur.writerows(lignes)
    return chemin


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        description="Vérifie en HTTP les candidats au retrait produits par audit_catalogue.py.",
    )
    parseur.add_argument("--site", default="all")
    parseur.add_argument("--limit", type=int, default=None,
                         help="ne vérifier que les N premiers (test)")
    parseur.add_argument("--concurrence", type=int, default=CONCURRENCE)
    args = parseur.parse_args(argv)

    fichiers = sorted(DOSSIER.glob("orphelins_*.txt"))
    if args.site != "all":
        fichiers = [f for f in fichiers if f.stem == f"orphelins_{args.site}"]
    if not fichiers:
        print(f"Aucun fichier d'orphelins dans {DOSSIER}.\n"
              f"Lancer d'abord : python audit_catalogue.py --out {DOSSIER}")
        return 1

    total_confirmes = total_verifies = 0
    for fichier in fichiers:
        site = fichier.stem.replace("orphelins_", "")
        urls = [u.strip() for u in fichier.read_text(encoding="utf-8").splitlines() if u.strip()]
        if args.limit:
            urls = urls[:args.limit]
        debut = time.monotonic()
        lignes = verifier(site, urls, args.concurrence)
        compte = _resumer(site, lignes, time.monotonic() - debut)
        chemin = _ecrire(site, lignes)
        print(f"    → verdicts écrits dans {chemin}")
        total_verifies += len(lignes)
        total_confirmes += sum(compte.get(v, 0) for v in VERDICTS_RETRAIT)

    print(f"\n─── {total_confirmes} retrait(s) confirmé(s) sur {total_verifies} candidat(s)")
    print("    Aucune écriture en base : ces verdicts sont une MESURE, pas une action.")
    return 0


if __name__ == "__main__":
    if str(DIRECTORY) not in sys.path:
        sys.path.insert(0, str(DIRECTORY))
    sys.exit(main())
