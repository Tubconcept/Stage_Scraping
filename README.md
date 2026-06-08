# Gestionnaire de Scrapers

Interface graphique Tkinter pour lancer les scrapers et exporter les données vers SQLite/CSV.

## Démarrage

```bash
uv run main.py
```

## Fournisseurs pris en charge

| Code | Site | Actions disponibles |
|------|------|---------------------|
| P1 | Legallais (`legallais.com`) | Produits · Commandes · Suivi · Suppression adresses |
| P3 | Prolians (`prolians.fr`) | Produits · Commandes · Suivi · Suppression adresses |
| P5 | Setin (`setin.fr`) | Produits · Commandes · Suivi · Suppression adresses |

## Configuration

Copiez `.env.example` en `.env` et renseignez vos identifiants :

```
User_P1=...   Password_P1=...   # Legallais
User_P3=...   Password_P3=...   # Prolians
User_P5=...   Password_P5=...   # Setin
```

Variable optionnelle : `DAYS_TO_SCRAPE` (défaut : 7).

## Bases de données

Chaque fournisseur dispose de son propre fichier SQLite à la racine :

| Fichier | Tables |
|---------|--------|
| `legallais.db` | `legallais_products` · `legallais_orders` · `legallais_tracking` |
| `prolians.db` | `prolians_products` · `prolians_orders` · `prolians_tracking` |
| `setin.db` | `setin_products` · `setin_orders` · `setin_tracking` |

Les schémas sont générés automatiquement au premier lancement depuis les constantes de `core/config.py`.

## Structure du projet

```
scrapers/
  Legallais_P1/   products · orders · tracking · deleting
  Prolians_P3/    products · orders · tracking · deleting
  Setin_P5/       products · orders · tracking · deleting
auth/             cookie managers par fournisseur
css_selectors/    sélecteurs CSS/XPath par fournisseur
core/             config · logger · utils
db/               sqlite_db.py (API SQLite partagée)
gui/              interface Tkinter
```

## Dépannage

**Erreur d'authentification** — vérifiez les identifiants dans `.env`.

**Scraper lent** — normal : chaque fiche/page est chargée dans un navigateur headless.

**Reprise après crash** — les URL produits déjà insérées en base sont ignorées au redémarrage.
