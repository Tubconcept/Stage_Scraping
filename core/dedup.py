"""
Identité des fiches produit — règle unique de déduplication.

Ce module répond à UNE question : « ces deux lignes décrivent-elles le même
article ? ». Il ne touche jamais à la base ; il calcule une clé et sait
fusionner deux lignes sans perdre d'information. La persistance est dans
db/mariadb_db.py (colonne ``product_uid`` + index UNIQUE).

Principe
--------
Chaque ligne produit reçoit un ``product_uid`` = SHA-1 d'une clé naturelle
construite à partir du premier critère renseigné parmi ceux du site
(CRITERES_PAR_SITE). Deux lignes de même uid = même article : la seconde
écriture met à jour la première au lieu d'en créer une nouvelle.

Pourquoi l'URL comme critère par défaut ?
-----------------------------------------
Mesuré sur les tables de production (juillet 2026) :

  - la référence fournisseur est FIABLE chez Sonepar (3 980 groupes de
    doublons, 100 % avec une désignation identique) → critère « ref » ;
  - elle est CONTAMINÉE chez Legallais et Sider : les lignes de déclinaison
    portent la référence du parent, si bien que 1 645 groupes P1 et 9 627
    groupes P6 regroupent des articles réellement différents (ex. le même
    « 145616 » pour un bloc 400 lm et un bloc 45 lm). Dédoublonner par
    référence y détruirait des articles → critère « url » ;
  - l'URL normalisée, elle, ne fusionne jamais deux articles distincts et
    récupère à elle seule 9 853 doublons P6 (alias ``#reference`` en fin
    d'URL).

Un site passera à « ref » le jour où son scraper écrira la bonne référence
sur chaque déclinaison : c'est une ligne à changer dans CRITERES_PAR_SITE.

API publique
------------
normaliser_url(url)                 → URL canonique (comparable)
normaliser_texte(txt)               → texte comparable (casse/espaces/accents)
cle_naturelle(site, row)            → clé lisible, ex. « sonepar|ref|05594004005 »
uid_produit(site, row)              → SHA-1 (40 car.) ou None si aucun critère
score_completude(row, colonnes)     → nb de champs renseignés (choix du survivant)
champs_modifies(base, neuve, cols)  → colonnes à écrire (fusion non destructive)
fusionner(base, neuve, colonnes)    → ligne fusionnée complète
sont_jumelles(a, b)                 → garde-fou avant fusion (mode strict)
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

#: Nom de la colonne portant l'empreinte d'unicité dans {PREFIX}_products.
COLONNE_UID = "product_uid"

#: Critères d'identité, dans l'ordre d'essai, pour chaque site.
#: Le premier critère dont la valeur est non vide fournit la clé naturelle.
CRITERES_PAR_SITE: dict[str, tuple[str, ...]] = {
    "legallais": ("url",),          # réf. contaminée par la réf. parente
    "prolians":  ("url",),
    "setin":     ("url",),          # la variante est dans ?idvar= → conservée
    "sider":     ("url",),          # normalisation d'URL = 9 853 doublons purgés
    "sonepar":   ("ref", "url"),    # réf. fiable (désignations 100 % identiques)
}

#: Critères appliqués à un site absent de CRITERES_PAR_SITE.
CRITERES_DEFAUT: tuple[str, ...] = ("url",)

#: Paramètres d'URL purement analytiques : ils ne changent pas l'article.
PARAMS_TRACKING: frozenset[str] = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_ga", "_gl",
    "ref_src", "igshid", "spm",
})

#: Colonnes lues par les critères d'identité et le garde-fou ``sont_jumelles``.
_COL_DESIGNATION = "product_designation"
_COL_EAN = "product_ean"
_COL_REF = "product_reference_fournisseur"
_COL_URL = "product_fournisseur_url"

_ESPACES = re.compile(r"\s+")
_NON_CHIFFRE = re.compile(r"\D+")


# ═══════════════════════════════════════════════════════════════════════════════
# NORMALISATION
# ═══════════════════════════════════════════════════════════════════════════════

def normaliser_url(url: str | None) -> str:
    """Ramène une URL à sa forme canonique, comparable d'un scrape à l'autre.

    - schéma et hôte en minuscules, ``www.`` conservé (il fait partie du site) ;
    - fragment ``#...`` supprimé : chez Sider il répète la référence et crée
      un alias de la même fiche ;
    - paramètres de tracking retirés, les autres conservés et triés
      (``?idvar=`` chez Setin identifie la déclinaison : le perdre fusionnerait
      des articles distincts) ;
    - slash final retiré.

    Args:
        url: URL brute telle que scrapée (peut être None ou vide).

    Returns:
        URL canonique, ou "" si l'entrée est vide.
    """
    brut = (url or "").strip()
    if not brut:
        return ""
    morceaux = urlsplit(brut)
    params = [
        (cle, val)
        for cle, val in parse_qsl(morceaux.query, keep_blank_values=True)
        if cle.lower() not in PARAMS_TRACKING
    ]
    params.sort()
    chemin = morceaux.path.rstrip("/") or "/"
    return urlunsplit((
        morceaux.scheme.lower(),
        morceaux.netloc.lower(),
        chemin,
        urlencode(params),
        "",  # fragment supprimé
    ))


def normaliser_texte(txt: str | None) -> str:
    """Rend un libellé comparable : sans accents, sans casse, espaces réduits."""
    brut = (txt or "").strip()
    if not brut:
        return ""
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFKD", brut) if not unicodedata.combining(c)
    )
    return _ESPACES.sub(" ", sans_accent).casefold()


def normaliser_reference(ref: str | None) -> str:
    """Normalise une référence fournisseur (espaces, casse)."""
    return _ESPACES.sub("", (ref or "").strip()).upper()


def normaliser_ean(ean: str | None) -> str:
    """Ne garde que les chiffres d'un EAN ; "" si le code est vide ou nul."""
    chiffres = _NON_CHIFFRE.sub("", str(ean or ""))
    return "" if not chiffres or set(chiffres) == {"0"} else chiffres


# ═══════════════════════════════════════════════════════════════════════════════
# CLÉ D'IDENTITÉ
# ═══════════════════════════════════════════════════════════════════════════════

def _valeur_critere(critere: str, row: dict) -> str:
    """Valeur normalisée d'un critère d'identité pour une ligne produit."""
    if critere == "url":
        return normaliser_url(row.get(_COL_URL))
    if critere == "ref":
        return normaliser_reference(row.get(_COL_REF))
    if critere == "ean":
        return normaliser_ean(row.get(_COL_EAN))
    raise ValueError(f"Critère d'identité inconnu : {critere!r}")


def criteres_du_site(site: str) -> tuple[str, ...]:
    """Critères d'identité configurés pour ce site (ou le défaut)."""
    return CRITERES_PAR_SITE.get(site, CRITERES_DEFAUT)


def cle_naturelle(site: str, row: dict, criteres: tuple[str, ...] | None = None) -> str:
    """Clé d'identité lisible, ex. ``sonepar|ref|05594004005``.

    Le premier critère renseigné gagne ; on renvoie "" si la ligne ne porte
    aucun identifiant exploitable (elle sera alors insérée sans garantie
    d'unicité plutôt que fusionnée à l'aveugle).
    """
    for critere in criteres or criteres_du_site(site):
        valeur = _valeur_critere(critere, row)
        if valeur:
            return f"{site}|{critere}|{valeur}"
    return ""


def uid_produit(site: str, row: dict, criteres: tuple[str, ...] | None = None) -> str | None:
    """Empreinte SHA-1 (40 caractères) de la clé naturelle, ou None.

    None signifie « aucun identifiant » : la colonne reste NULL en base, et
    NULL n'entre jamais en conflit dans un index UNIQUE MariaDB.
    """
    cle = cle_naturelle(site, row, criteres)
    if not cle:
        return None
    return hashlib.sha1(cle.encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
# FUSION NON DESTRUCTIVE
# ═══════════════════════════════════════════════════════════════════════════════

def _valeur(row: dict, colonne: str) -> str:
    return str(row.get(colonne, "") or "").strip()


def score_completude(row: dict, colonnes: list[str]) -> tuple[int, int]:
    """Score de richesse d'une ligne : (champs renseignés, volume de texte).

    Sert à désigner le survivant d'un groupe de doublons : on garde la fiche
    la plus complète, pas la plus ancienne.
    """
    remplis = sum(1 for c in colonnes if _valeur(row, c))
    volume = sum(len(_valeur(row, c)) for c in colonnes)
    return remplis, volume


def champs_modifies(base: dict, nouvelle: dict, colonnes: list[str],
                    figees: frozenset[str] = frozenset()) -> dict:
    """Colonnes à réécrire pour enrichir ``base`` avec ``nouvelle``.

    Règle non destructive : une valeur vide n'écrase jamais une valeur
    existante. Un champ déjà renseigné est rafraîchi si le nouveau scrape
    apporte une valeur différente (prix, stock…).

    Args:
        base: ligne actuellement en base.
        nouvelle: ligne fraîchement scrapée.
        colonnes: colonnes candidates (CSV_HEADERS).
        figees: colonnes à ne jamais réécrire (ex. l'URL canonique, qui porte
            un index UNIQUE et dont la réécriture provoquerait une collision).

    Returns:
        Dict des seules colonnes à mettre à jour (vide si rien à faire).
    """
    maj: dict[str, str] = {}
    for colonne in colonnes:
        if colonne in figees:
            continue
        neuve = _valeur(nouvelle, colonne)
        if not neuve or neuve == _valeur(base, colonne):
            continue
        maj[colonne] = neuve
    return maj


def fusionner(base: dict, nouvelle: dict, colonnes: list[str],
              figees: frozenset[str] = frozenset()) -> dict:
    """Ligne complète issue de la fusion non destructive de deux lignes."""
    fusion = {c: _valeur(base, c) for c in colonnes}
    fusion.update(champs_modifies(base, nouvelle, colonnes, figees))
    return fusion


def sont_jumelles(a: dict, b: dict) -> bool:
    """Garde-fou : deux lignes décrivent-elles visiblement le même article ?

    Utilisé par le mode strict du dédoublonnage pour refuser de fusionner des
    lignes qui partagent une clé mais divergent sur le fond — typiquement les
    références contaminées de Legallais et Sider, où le même identifiant
    désigne deux articles réellement différents.

    Vrai tant qu'aucun signal d'identité ne se contredit. Les champs VOLATILS
    (prix, stock) sont délibérément exclus : deux scrapes du même article à
    deux dates affichent des prix différents, et c'est précisément ce que la
    fusion doit rafraîchir — pas un motif de refus.
    """
    ean_a, ean_b = normaliser_ean(a.get(_COL_EAN)), normaliser_ean(b.get(_COL_EAN))
    if ean_a and ean_b:
        return ean_a == ean_b

    ref_a, ref_b = normaliser_reference(a.get(_COL_REF)), normaliser_reference(b.get(_COL_REF))
    if ref_a and ref_b and ref_a != ref_b:
        return False

    des_a = normaliser_texte(a.get(_COL_DESIGNATION))
    des_b = normaliser_texte(b.get(_COL_DESIGNATION))
    if des_a and des_b and des_a != des_b:
        # Titres remaniés par le fournisseur : on tolère l'inclusion
        # (« … - legrand » vs « … - legrand legrand ») mais rien de plus.
        if not (des_a.startswith(des_b) or des_b.startswith(des_a)):
            return False

    return True


def _prix(row: dict) -> float | None:
    """Prix HT en float, ou None si absent/illisible (formats « 1 426,74 », « 12.5 € »)."""
    brut = _valeur(row, _COL_PRIX)
    if not brut:
        return None
    nettoye = brut.replace(" ", "").replace("\xa0", "").replace(" ", "")
    nettoye = nettoye.replace("€", "").replace(",", ".")
    try:
        return round(float(nettoye), 2)
    except ValueError:
        return None
