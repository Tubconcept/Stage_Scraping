"""
HTTP « poli » pour les scrapes HTTP en volume — réduit la détection bot.

Fournit :
  - ``build_browser_headers`` : en-têtes mimant un vrai navigateur Chrome, à garder
    COHÉRENTS avec la session (passer le ``User-Agent`` du navigateur qui a ouvert la
    session — ``navigator.userAgent`` — pour qu'en-têtes et cookies concordent).
  - ``polite_get`` : GET avec **délai aléatoire (jitter)** avant la requête +
    **retry à back-off exponentiel** sur 429/403/503 (rate-limit / blocage) +
    décompression gzip.

À utiliser pour les fetchers haut volume (catalogues) au lieu d'un ``urlopen`` nu
en rafale. Le facteur anti-détection le plus important reste le **débit** : borner
la concurrence (peu de workers) et garder un ``delay`` non nul.
"""
from __future__ import annotations

import gzip
import random
import time
import urllib.error
import urllib.request

# UA de repli (Chrome récent) si on ne fournit pas celui du navigateur de session.
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def build_browser_headers(
    ua: str | None = None,
    cookie: str | None = None,
    referer: str | None = None,
    accept_language: str = "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
) -> dict:
    """En-têtes réalistes type Chrome.

    ``ua`` : passer l'User-Agent du navigateur de session (cohérence avec ``cookie``) ;
    à défaut, un UA Chrome récent. ``referer`` : page d'origine plausible (ex. accueil
    du site) pour ne pas arriver « sans provenance ».
    """
    headers = {
        "User-Agent": ua or _DEFAULT_UA,
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,image/apng,*/*;q=0.8"),
        "Accept-Language": accept_language,
        "Accept-Encoding": "gzip",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Connection": "keep-alive",
    }
    if referer:
        headers["Referer"] = referer
    if cookie:
        headers["Cookie"] = cookie
    return headers


def polite_get(
    url: str,
    headers: dict,
    *,
    timeout: int = 30,
    delay: tuple[float, float] | None = (0.3, 0.9),
    retries: int = 3,
    should_stop=None,
) -> str | None:
    """GET « poli » : jitter avant requête + retry/back-off sur rate-limit/blocage.

    Args:
        url, headers : la requête (headers via ``build_browser_headers``).
        timeout : timeout réseau par tentative (s).
        delay : (min, max) attente ALÉATOIRE avant la requête — espacement humain ;
            ``None`` pour désactiver.
        retries : tentatives supplémentaires sur 429/403/503 (back-off exponentiel
            + jitter) et erreurs réseau transitoires.
        should_stop : callable optionnel → True pour annuler proprement.

    Returns:
        HTML décodé (str), ou None en cas d'échec / d'arrêt.
    """
    if delay:
        time.sleep(random.uniform(*delay))
    for attempt in range(retries + 1):
        if should_stop and should_stop():
            return None
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip" or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            # 429 = trop de requêtes, 403/503 = blocage/indispo temporaire → on recule
            if exc.code in (429, 403, 503) and attempt < retries:
                time.sleep(2.0 ** attempt + random.uniform(0.0, 1.5))
                continue
            return None
        except Exception:
            if attempt < retries:
                time.sleep(1.5 ** attempt + random.uniform(0.0, 0.5))
                continue
            return None
    return None
