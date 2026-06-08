"""
Package racine des scrapers Prolians (fournisseur P3).

Regroupe les sous-modules métier du site prolians.fr :
- products : catalogue produits via sitemaps et fiches article ;
- orders   : historique des commandes client ;
- tracking : suivi colis et transporteurs ;
- deleting : nettoyage des adresses enregistrées (suppradrr).

Chaque sous-package expose un point d'entrée ``scrap_*.py`` (orchestration)
et un moteur ``scraper_*.py`` (sélecteurs CSS / parsing DOM).
"""
