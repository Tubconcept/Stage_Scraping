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

Les scrapers écrivent dans **MariaDB** (`db/mariadb_db.py`, base `Scraper_base`, connexion via les variables `DB_*` du `.env`). Une table par fournisseur et par nature de données, préfixée par le code fournisseur :

| Préfixe | Fournisseur | Tables |
|---------|-------------|--------|
| `P1` | Legallais | `P1_products` · `P1_orders` · `P1_tracking` |
| `P3` | Prolians | `P3_products` · `P3_orders` · `P3_tracking` |
| `P5` | Setin | `P5_products` · `P5_orders` · `P5_tracking` |
| `P6` | Sider | `P6_products` · `P6_orders` · `P6_tracking` |
| `P8` | Sonepar | `P8_products` |

Les schémas sont générés automatiquement au premier lancement depuis les constantes de `core/config.py`. `db/sqlite_db.py` conserve la même API pour un usage local hors ligne.

### Unicité des fiches produit

Chaque ligne produit porte un `product_uid` (empreinte de son identité, sous index UNIQUE). Toute écriture passe par `db.mariadb_db.save_product` : une fiche déjà connue est **enrichie**, jamais dupliquée, et la fusion est non destructive — une valeur vide n'écrase jamais une valeur déjà en base, tandis que prix et stock sont rafraîchis.

Ce qui fait qu'une fiche est « la même » est défini par site dans `CRITERES_PAR_SITE` (`core/dedup.py`) : l'URL normalisée partout, la référence fournisseur chez Sonepar. Voir le module pour le pourquoi de ce choix, mesuré sur les données réelles.

Une passe de dédoublonnage s'exécute **automatiquement à la fin de chaque scrape produits** ; elle ne fusionne que les cas non ambigus et signale les autres dans le journal. Pour la lancer à la main :

```bash
python dedoublonnage.py                        # simulation, tous les sites
python dedoublonnage.py --site sider --apply    # applique sur un site
python dedoublonnage.py --site prolians --criteres ref   # audit par référence
```

## Méthodes rapides (sitemap / API)

À côté des scrapers historiques (parcours du menu de catégories, une page
navigateur par fiche), l'action **« Méthodes »** de la GUI expose des voies
portées de SCRAPPER_App : énumération par **sitemap XML** ou **API interne**, puis
écriture directe en base par `save_product`. La voie est un **paramètre**, pas un
bouton de plus — un seul sélecteur, alimenté par `core/methodes.py`.

| Fournisseur | Méthode | Ce qu'elle fait |
|-------------|---------|-----------------|
| Prolians P3 | `api` | Sitemap + GraphQL `productsByReferences` batché par 100 : prix, stock, marque, fil d'Ariane **et** description, images, caractéristiques, éco-participation. La requête riche passe **en anonyme** — elle survit à une session expirée. |
| Setin P5 | `sitemap` | Sitemap (~20 000 fiches) + variables JS inline : prix **numérique**, quantité de stock, EAN, réf fabricant, sans ouvrir une page par fiche. Tarifs au-delà de 10 variantes complétés par `load_prices.php`. |
| Legallais P1 | `sitemap` | Sitemap (~48 000 fiches) + `/get-article-infos/<code>` pour le prix compte. **Passe d'enrichissement** : les fiches « gamme » chargent leur tableau en JS et n'exposent aucun code en statique. |

Ces méthodes traitent chaque fiche comme un **article simple** : elles ne
produisent aucune colonne de déclinaison (`products_is_combination`,
`product_parent_reference`…). Les voies historiques continuent de les renseigner,
et la fusion non destructive de `save_product` les préserve.

Garde-fous embarqués : battement de progression (l'interface ne paraît jamais
figée), énumération du sitemap **en thread** (elle ne gèle pas la boucle async),
point de reprise par valeur à chaque lot, re-login à chaud sur session perdue et
arrêt sur falaise (Setin), et refus de dégrader en silence côté Prolians — un
catalogue demandé *avec* enrichissement qui partirait sans prix est un échec, pas
un résultat.

⚠️ Legallais n'a pas d'auto-login (captcha proof-of-work) : se connecter d'abord
via `auth/legallais/manual_login_legallais.py`, sinon la méthode émet des fiches
publiques sans prix.

## Structure du projet

```
scrapers/
  Legallais_P1/   products · orders · tracking · deleting
  Prolians_P3/    products · orders · tracking · deleting
  Setin_P5/       products · orders · tracking · deleting
  Sider_P6/       products
  Sonepar_P8/     products
                  (methode_*.py = voies rapides ; *_sitemap / *_fiche_* = briques pures)
auth/             cookie managers par fournisseur
css_selectors/    sélecteurs CSS/XPath par fournisseur
core/             config · logger · utils · texte
                  dedup (identité des fiches) · f2 (mapping colonnes)
                  scrap_base · sink · methodes (socle des voies rapides)
                  http_poli · reprise · sessions · login_auto · garde_session
db/               mariadb_db.py (production) · sqlite_db.py (local)
gui/              interface Tkinter
dedoublonnage.py  outil de dédoublonnage en ligne de commande
```

## Dépannage

**Erreur d'authentification** — vérifiez les identifiants dans `.env`.

**Scraper lent** — normal : chaque fiche/page est chargée dans un navigateur headless.

**Reprise après crash** — les URL produits déjà insérées en base sont ignorées au redémarrage.
