"""
Auto-login fournisseur depuis le ``.env`` — sites **sans captcha**.

Sert au **re-login à chaud** des méthodes légères (cf. ``core/garde_session.py``) :
les sessions B2B sont courtes (Setin en particulier), et un run de plusieurs
heures qui perd sa session sert des prix publics à la place des prix compte.
Pouvoir se reconnecter sans intervention humaine est ce qui rend ces runs longs
exploitables.

Les identifiants ne sont **jamais en dur** : ``CONFIG_LOGIN`` nomme les variables
``.env`` (``User_P5``/``Password_P5``…), lues au runtime.

⚠️ **Legallais est hors périmètre** : son login passe un captcha proof-of-work et
demande le moteur Botasaurus. ``login_auto("legallais")`` renvoie donc ``False``,
et le garde-fou de falaise arrête proprement le run avec un message explicite —
plutôt que de boucler. Le login Legallais reste manuel
(``auth/legallais/manual_login_legallais.py``).

Module **structurel** (ouvre un vrai navigateur) ; les helpers de configuration
sont purs et testés.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from core.config import DIRECTORY

_log = logging.getLogger(__name__)

# Forçage d'ouverture d'un off-canvas Foundation (repli si le clic n'ouvre pas le form).
_JS_OUVRIR_OFFCANVAS = (
    "(id) => { const oc = document.getElementById(id); if (oc) { "
    "oc.classList.add('is-open','is-transition-overlap'); oc.style.visibility='visible'; "
    "oc.style.transform='none'; oc.style.left='0'; } }"
)


@dataclass(frozen=True)
class ConfigLoginAuto:
    """Sélecteurs du formulaire de login + noms des variables ``.env``.

    ``ouvrir`` : sélecteur pour faire apparaître le formulaire (icône compte…) ;
    ``None`` = l'URL **est** la page de login. ``offcanvas_id`` : id d'un panneau
    Foundation à forcer visible si le clic n'ouvre rien.
    """

    champ_email: str
    champ_password: str
    submit: str
    var_user: str
    var_password: str
    ouvrir: str | None = None
    cookies: str | None = None
    offcanvas_id: str | None = None
    #: Login en 2 étapes (Prolians) : le mot de passe n'apparaît qu'après validation
    #: de l'email.
    email_puis_valider: bool = False
    #: Bouton de la **2ᵉ** étape quand il diffère du premier. ``None`` = même bouton.
    #: Prolians a deux libellés distincts (« Connexion / Inscription » puis
    #: « Se connecter ») : un sélecteur unique ne peut pas couvrir les deux.
    submit_etape2: str | None = None


@dataclass(frozen=True)
class ConfigLogin:
    """Comment se loguer : page de connexion + moteur + preuve de connexion."""

    url: str
    moteur: str = "playwright"  # "playwright" | "botasaurus"
    #: Sélecteur présent **une fois connecté** : la session n'est sauvegardée que
    #: s'il apparaît → un login raté n'écrase jamais une bonne session.
    selecteur_connecte: str | None = None
    auto: ConfigLoginAuto | None = None


#: Descripteurs de login par fournisseur. Sélecteurs sondés en live (SCRAPPER_App,
#: juillet 2026) — les revalider si un site refond son formulaire.
CONFIG_LOGIN: dict[str, ConfigLogin] = {
    # Prolians (P3) — sans captcha mais en 2 étapes (React Aria) : /login n'affiche
    # que l'email ; le mot de passe apparaît après validation. Ids DOM aléatoires
    # (react-aria) → cibler par name/type, jamais par id.
    "prolians": ConfigLogin(
        url="https://www.prolians.fr/login",
        selecteur_connecte="button[aria-label='Mon compte']",
        auto=ConfigLoginAuto(
            champ_email="input[name='email']",
            # ``data-testid`` d'abord : /login porte aussi un champ « mot de passe
            # provisoire » à l'inscription, et son ``id``/``name`` change au gré des
            # libellés (relevé 06/08/2026 : name="Saisissez votre mot de passe
            # provisoire"). Le ``type`` reste en repli.
            champ_password="input[data-testid='password'], input[type='password']",
            # ⚠️ Les DEUX étapes ont des boutons DIFFÉRENTS (relevé 06/08/2026) :
            # « Connexion / Inscription » puis « Se connecter ». Le code rejouait le
            # MÊME sélecteur aux deux étapes — ``button[data-testid='button']``, qui
            # matche toujours l'étape 1 (vérifié en live le 06/08) mais pas le bouton
            # final, structuré différemment (<span> nu au lieu de <span class="btn-text">).
            # Ciblage par TEXTE : les ids sont générés par react-aria (« «r53» ») et
            # les data-testid ont déjà bougé une fois.
            submit="button:has-text('Connexion / Inscription')",
            submit_etape2="button:has-text('Se connecter')",
            var_user="User_P3",
            var_password="Password_P3",
            email_puis_valider=True,
            # ⚠️ Plus aucun bandeau cookies sur /login au 06/08/2026 (0 correspondance,
            # toutes variantes confondues). Conservé : le clic est enveloppé d'un
            # ``suppress``, donc sans effet s'il a disparu — et il peut réapparaître
            # selon l'état de consentement ou la géolocalisation.
            cookies="button:has-text('Accepter & Fermer')",
        ),
    ),
    # Setin (P5) — login plein sans captcha. Le form est dans un off-canvas
    # Foundation ouvert par l'icône compte. Sessions B2B courtes → auto-login précieux.
    "setin": ConfigLogin(
        url="https://www.setin.fr/",
        selecteur_connecte="div.info-perso",
        auto=ConfigLoginAuto(
            cookies="a.AcceptAllBouton",
            ouvrir="a:has(img[alt='Accès à mon compte'])",
            offcanvas_id="offCanvasCompte",
            champ_email="input#acces_mail",
            champ_password="input#acces_password",
            submit="a.jqBtnConnection",
            var_user="User_P5",
            var_password="Password_P5",
        ),
    ),
    # Sider (P6) — /login porte AUSSI le formulaire d'inscription (#email/#password) :
    # cibler les ids préfixés « _ » du formulaire de CONNEXION.
    "sider": ConfigLogin(
        url="https://www.sider.biz/login",
        selecteur_connecte="a[href*='logout'], a[href*='deconnexion'], a[href*='mon-compte']",
        auto=ConfigLoginAuto(
            cookies="button#didomi-notice-agree-button",
            champ_email="input#_email",
            champ_password="input#_password",
            submit="button#login",
            var_user="User_P6",
            var_password="Password_P6",
        ),
    ),
    # Sonepar (P8) — login Azure B2C : le clic navigue vers login.sonepar.fr.
    "sonepar": ConfigLogin(
        url="https://www.sonepar.fr/fr-fr",
        selecteur_connecte="[data-testid*='account'], a[href*='mon-compte']",
        auto=ConfigLoginAuto(
            cookies="#onetrust-accept-btn-handler",
            ouvrir="[data-testid='login-button']",
            champ_email="input#email",
            champ_password="input#password",
            submit="button#next",
            var_user="User_P8",
            var_password="Password_P8",
        ),
    ),
    # Legallais (P1) — captcha proof-of-work → moteur Botasaurus, login MANUEL ici.
    # Pas de bloc ``auto`` : login_auto refusera explicitement plutôt que d'échouer
    # en boucle sur un formulaire qu'il ne sait pas franchir.
    "legallais": ConfigLogin(
        url="https://www.legallais.com/user/connection",
        moteur="botasaurus",
        selecteur_connecte="a[href*='logout'], a[href*='deconnexion'], a[href*='disconnect']",
    ),
}


def config_login(fournisseur: str) -> ConfigLogin | None:
    """Descripteur de login d'un fournisseur, ou ``None`` s'il est inconnu."""
    return CONFIG_LOGIN.get(fournisseur)


def identifiants(config: ConfigLoginAuto,
                 environ: dict[str, str] | None = None) -> tuple[str, str]:
    """(user, mot de passe) lus dans l'environnement. **Pur** si ``environ`` est fourni.

    Sans ``environ``, charge le ``.env`` du projet au moment de l'appel — pas à
    l'import, pour qu'importer ce module reste sans effet de bord.
    """
    if environ is None:
        load_dotenv(DIRECTORY / ".env")
        environ = dict(os.environ)
    return (environ.get(config.var_user, ""), environ.get(config.var_password, ""))


# ─── Login automatique (Playwright) ──────────────────────────────────────────

async def _est_connecte(contexte, selecteur_connecte: str | None) -> bool:
    """Vrai si l'état connecté est détecté sur une page du contexte."""
    if selecteur_connecte is None:
        return True
    for page in contexte.pages:
        try:
            if await page.locator(selecteur_connecte).count() > 0:
                return True
        except Exception:  # page fermée / en navigation : on tente la suivante
            continue
    return False


async def _champ_visible(page, selecteur: str):
    """Localise le 1ᵉʳ élément **visible** du sélecteur (repli : le 1ᵉʳ du DOM).

    Gère les formulaires dupliqués (mobile + desktop) où seul l'un est affiché.
    """
    loc = page.locator(f"{selecteur}:visible").first
    return loc if await loc.count() else page.locator(selecteur).first


async def _ouvrir_formulaire(page, config: ConfigLoginAuto) -> None:
    """Accepte les cookies puis fait apparaître le formulaire de connexion."""
    if config.cookies:
        bandeau = page.locator(config.cookies).first
        if await bandeau.count():
            with contextlib.suppress(Exception):
                await bandeau.click(timeout=4000)
                await page.wait_for_timeout(600)
    email = page.locator(config.champ_email).first
    if config.ouvrir:  # None = l'URL est déjà la page de login
        declencheur = page.locator(config.ouvrir).first
        if await declencheur.count():
            with contextlib.suppress(Exception):
                await declencheur.click(timeout=5000)
                # Peut déclencher une navigation complète (Sonepar → login.sonepar.fr).
                await page.wait_for_timeout(2500)
    if not await email.is_visible() and config.offcanvas_id:
        with contextlib.suppress(Exception):
            await page.evaluate(_JS_OUVRIR_OFFCANVAS, config.offcanvas_id)
            await page.wait_for_timeout(1000)


async def _valider(page, selecteur: str, etape: str) -> None:
    """Clique le bouton de soumission ; **replie sur Entrée** et trace tout échec.

    Ce clic ne doit jamais être silencieux : quand le sélecteur ne correspond
    plus à rien, l'erreur remonte deux étapes plus loin et envoie le diagnostic
    sur une fausse piste. Le repli clavier vaut mieux qu'un échec — un formulaire
    dont le bouton a bougé se soumet presque toujours par Entrée, le champ ayant
    le focus après le ``fill``.
    """
    bouton = page.locator(selecteur).first
    try:
        if await bouton.count():
            await bouton.click(timeout=6000, no_wait_after=True)
            return
        _log.warning("Auto-login : bouton « %s » introuvable (%s) — repli sur Entrée.",
                     selecteur, etape)
    except Exception as exc:
        _log.warning("Auto-login : clic sur « %s » en échec (%s) : %s — repli sur Entrée.",
                     selecteur, etape, exc)
    with contextlib.suppress(Exception):
        await page.keyboard.press("Enter")


async def _attendre_connecte(contexte, selecteur: str | None, *,
                             essais: int = 20, intervalle_s: float = 2.0) -> bool:
    for _ in range(essais):
        if await _est_connecte(contexte, selecteur):
            return True
        await asyncio.sleep(intervalle_s)
    return False


async def login_auto(fournisseur: str, chemin_session: Path,
                     *, headless: bool = False) -> bool:
    """Auto-login d'un fournisseur sans captcha ; écrit la session **si connecté**.

    Args:
        fournisseur: clé de ``CONFIG_LOGIN`` (« setin », « prolians »…).
        chemin_session: où écrire le ``storage_state``.
        headless: visible par défaut — un login qui échoue doit pouvoir être
            regardé (formulaire modifié, MFA, captcha inattendu).

    Returns:
        ``True`` si la connexion a été détectée et la session sauvegardée.
        ``False`` sans auto-login configuré (Legallais) ou sans identifiants —
        la session existante reste alors **intacte**.
    """
    config = config_login(fournisseur)
    if config is None or config.auto is None:
        _log.warning("Auto-login indisponible pour %s (login manuel requis).", fournisseur)
        return False

    user, mdp = identifiants(config.auto)
    if not (user and mdp):
        _log.warning("Auto-login %s : identifiants absents (%s/%s dans .env).",
                     fournisseur, config.auto.var_user, config.auto.var_password)
        return False

    from playwright.async_api import async_playwright

    chemin_session = Path(chemin_session)
    chemin_session.parent.mkdir(parents=True, exist_ok=True)
    connecte = False
    async with async_playwright() as pw:
        navigateur = await pw.chromium.launch(headless=headless)
        contexte = await navigateur.new_context()
        try:
            page = await contexte.new_page()
            await page.goto(config.url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            await _ouvrir_formulaire(page, config.auto)
            await (await _champ_visible(page, config.auto.champ_email)).fill(user, timeout=6000)
            if config.auto.email_puis_valider:
                await _valider(page, config.auto.submit, "validation de l'email (étape 1/2)")
                await page.wait_for_timeout(1500)
            await (await _champ_visible(page, config.auto.champ_password)).fill(
                mdp, timeout=12000 if config.auto.email_puis_valider else 6000
            )
            await _valider(page, config.auto.submit_etape2 or config.auto.submit,
                           "soumission du mot de passe")
            connecte = await _attendre_connecte(contexte, config.selecteur_connecte)
            if connecte:
                etat = await contexte.storage_state()
                chemin_session.write_text(
                    json.dumps(etat, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        finally:
            with contextlib.suppress(Exception):
                await contexte.close()
            with contextlib.suppress(Exception):
                await navigateur.close()

    if not connecte:
        _log.warning("Auto-login %s non connecté : session inchangée.", fournisseur)
    return connecte
