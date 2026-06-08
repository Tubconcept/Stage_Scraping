"""
Package auth — gestion de l'authentification par fournisseur.

Sous-packages :
  - auth.setin     : session Playwright (playwright_profiles/setin/session.json)
  - auth.legallais : session Playwright + helpers Botasaurus (legallais/session.json)
  - auth.prolians  : cookies Playwright (prolians/session.json)

Chaque cookie_manager expose ensure_logged_in(page, context, user, password)
à appeler au début de tout scraper nécessitant une connexion client.

Scripts manuels (manual_login*.py) : à lancer une fois pour créer/renouveler la session.
"""
