# Ajout d’un nouveau fournisseur / site

Documentation d’intégration pour développeurs expérimentés.  
Objectif : brancher un fournisseur dans l’application et le rendre sélectionnable dans l’interface — **sans** détailler l’implémentation des scrapers.

---

## 1. Vue d’ensemble

Un fournisseur est intégré en trois couches distinctes :

| Couche | Rôle |
|--------|------|
| **Registre GUI** | `gui/site_config.py` → dictionnaire `SITES_CONFIG` : nom affiché, catégories, chemins des modules `scrap_*` par mode. |
| **Lanceur GUI** | `gui/interface.py` : routage `_launch()` → méthode `_launch_<Site>()`, import dynamique, threads async/sync. |
| **Persistance** | `db/sqlite_db.py` → `SITE_DB_PATHS` : une base `{site}.db` par fournisseur, tables `{site}_products`, `{site}_orders`, `{site}_tracking`. |

**Point d’entrée utilisateur :** `main.py` instancie `ScraperApp` (`gui/interface.py`).

**Flux utilisateur :**

1. Boutons de site générés depuis les clés de `SITES_CONFIG`.
2. Quatre actions fixes : Produits, Commandes, Suivi Commandes, Suppression Adresses (`produits`, `commandes`, `suivi`, `suppr`).
3. Au lancement, `importlib.import_module()` charge le module indiqué dans `imports`, puis la GUI appelle le point d’entrée adapté (`create_scraper()`, `main()`, ou un wrapper dédié dans `interface.py`).

> **Important :** ajouter une entrée dans `SITES_CONFIG` affiche le site dans l’UI, mais **ne suffit pas** pour l’exécuter. Il faut aussi implémenter le routage dans `gui/interface.py` (voir § 5).

Les schémas SQLite sont dérivés des en-têtes globaux `CSV_HEADERS`, `ORDERS_CSV_HEADERS`, `TRACKING_CSV_HEADERS` dans `core/config.py` — aucune définition de colonnes par site.

---

## 2. Fichiers à créer

Convention observée : code fournisseur `P{n}` (ex. P1 Legallais, P3 Prolians, P5 Setin), nom de dossier `{NomSite}_P{n}`.

### 2.1 Scrapers (obligatoire pour les 4 modes UI)

Pour chaque mode, le projet sépare **orchestrateur** (`scrap_*`) et **moteur DOM** (`scraper_*`). Seuls les chemins `scrap_*` sont référencés par la GUI.

| Fichier | Emplacement type | Rôle (intégration) |
|---------|------------------|---------------------|
| `scrap_{site}_products.py` | `scrapers/{Site}_P{n}/products/` | Point d’entrée mode **Produits** ; doit être importable via `SITES_CONFIG["imports"]["produits"]`. |
| `scraper_{site}_products.py` | `scrapers/{Site}_P{n}/products/` | Moteur produits (non référencé directement par la GUI). |
| `scrap_{site}_orders.py` | `scrapers/{Site}_P{n}/orders/` | Point d’entrée mode **Commandes**. |
| `scraper_{site}_orders.py` | `scrapers/{Site}_P{n}/orders/` | Moteur commandes. |
| `scrap_{site}_tracking.py` | `scrapers/{Site}_P{n}/tracking/` | Point d’entrée mode **Suivi**. |
| `scraper_{site}_tracking.py` | `scrapers/{Site}_P{n}/tracking/` | Moteur suivi. |
| `scrap_{site}_suppradrr*.py` | `scrapers/{Site}_P{n}/deleting/` | Point d’entrée mode **Suppression Adresses** (nom variable selon le site, ex. `scrap_suppradrr_p5.py`). |

Fichiers `__init__.py` : présents sur Prolians (`scrapers/Prolians_P3/` et sous-dossiers) ; optionnels sur Legallais/Setin. Les créer si le packaging l’exige.

Chaque `scrap_*.py` inclut en tête un bootstrap `sys.path` vers la racine du dépôt (`parents[3]` depuis un fichier à 4 niveaux de profondeur).

### 2.2 Sélecteurs (si filtre catégorie produits)

| Fichier | Emplacement | Rôle |
|---------|-------------|------|
| `selectors/{site}.py` | `selectors/` | Constantes / classe `Selectors` ; pour les catégories produits, exporter `CATEGORY_NAMES` (Legallais) ou `Selectors.CATEGORY_NAMES` (Setin). |

Référencé dans `gui/site_config.py` pour remplir le combobox catégories lorsque `has_categories: True`.

### 2.3 Authentification

| Fichier | Emplacement | Rôle |
|---------|-------------|------|
| `cookie_manager*.py` | `auth/{site}/` | `ensure_logged_in(page, context, user, password)` — contrat commun documenté dans `auth/__init__.py`. |
| `manual_login*.py` | `auth/{site}/` | Script CLI ponctuel pour créer/renouveler la session navigateur. |

Session Playwright typique : `playwright_profiles/{site}/session.json` (créé à l’exécution, pas versionné).

Setin utilise aussi `playwright_profiles/setin_storage.json` via `auth/setin/manual_login_setin.py` — pattern spécifique à ce fournisseur.

### 2.4 Fichiers générés à l’exécution (pas à committer)

| Fichier | Emplacement | Rôle |
|---------|-------------|------|
| `{site}.db` | Racine du dépôt | Base SQLite du fournisseur (clé en minuscules, ex. `nouveausite.db`). |
| `session.json` | `playwright_profiles/{site}/` | Session navigateur. |

### 2.5 Variables d’environnement

| Fichier | Rôle |
|---------|------|
| `.env` (local) | `User_P{n}` et `Password_P{n}` pour le compte B2B du fournisseur. |
| `.env.example` | Modèle à documenter pour le nouveau couple de variables. |

Les scrapers et la GUI lisent ces variables ; elles ne sont pas centralisées dans `core/config.py`.

---

## 3. Arborescence cible

Exemple pour un fournisseur **Acme** code **P7** :

```text
Stage_Scraping/
├── main.py
├── .env / .env.example
├── {site}.db                          # créé au premier scrape
│
├── auth/
│   └── acme/
│       ├── cookie_manager.py          # ou cookie_manager_acme.py
│       └── manual_login.py
│
├── selectors/
│   └── acme.py                        # si has_categories
│
├── playwright_profiles/
│   └── acme/
│       └── session.json               # généré
│
├── scrapers/
│   └── Acme_P7/
│       ├── products/
│       │   ├── scrap_acme_products.py
│       │   └── scraper_acme_products.py
│       ├── orders/
│       │   ├── scrap_acme_orders.py
│       │   └── scraper_acme_orders.py
│       ├── tracking/
│       │   ├── scrap_acme_tracking.py
│       │   └── scraper_acme_tracking.py
│       └── deleting/
│           └── scrap_acme_suppradrr.py
│
├── gui/
│   ├── site_config.py                 # ← enregistrement
│   └── interface.py                   # ← lanceur + export CSV
│
├── db/
│   └── sqlite_db.py                   # ← SITE_DB_PATHS
│
└── core/
    └── config.py                      # ← optionnel : constante *_DB_PATH
```

Référence réelle (fournisseurs existants) :

```text
scrapers/
├── Legallais_P1/
│   ├── products/   scrap_legallais_products.py, scraper_legallais_products.py
│   ├── orders/     scrap_legallais_orders.py, scraper_legallais_orders.py
│   ├── tracking/   scrap_legallais_tracking.py, scraper_legallais_tracking.py
│   └── deleting/   scrap_legallais_suppradrr.py
├── Prolians_P3/
│   └── … (même découpage + __init__.py)
└── Setin_P5/
    └── … (même découpage)
```

---

## 4. Fichiers existants à modifier

### 4.1 `gui/site_config.py` — **enregistrement principal**

**Pourquoi :** source unique du registre des sites pour l’interface.

**À ajouter :** une entrée dans `SITES_CONFIG` :

```python
"Acme": {
    "has_categories": True,   # ou False
    "categories": _AcmeCategories,  # import depuis selectors/acme.py
    "imports": {
        "produits":  "scrapers.Acme_P7.products.scrap_acme_products",
        "commandes": "scrapers.Acme_P7.orders.scrap_acme_orders",
        "suivi":     "scrapers.Acme_P7.tracking.scrap_acme_tracking",
        "suppr":     "scrapers.Acme_P7.deleting.scrap_acme_suppradrr",
    },
},
```

- La **clé** du dictionnaire (`"Acme"`) est le libellé des boutons site dans la GUI.
- Les clés de `imports` sont fixes : `produits`, `commandes`, `suivi`, `suppr`.
- Les valeurs sont des **chemins de module Python** (notation pointée), pas des chemins fichiers.

### 4.2 `gui/interface.py` — **exécution depuis l’UI**

**Pourquoi :** le choix du site dans `SITES_CONFIG` ne déclenche pas automatiquement un lanceur générique.

**Modifications nécessaires :**

| Zone | Modification |
|------|----------------|
| `_launch()` (≈ L471) | Ajouter une branche `elif self._site == "Acme": self._launch_acme(key)` (aujourd’hui : Setin, Legallais, sinon Prolians). |
| Nouvelle méthode `_launch_acme()` | Pour chaque `key` (`produits`…`suppr`), charger `SITES_CONFIG["Acme"]["imports"][key]` et appeler le bon mécanisme (voir § 5.2). |
| `_download()` — `_SITE_KEYS` (≈ L519) | Associer le nom GUI au slug SQLite : `"Acme": "acme"`. |
| Wrappers sync (optionnel) | Si le scraper utilise `input()` en CLI ou n’expose pas `create_scraper()`, ajouter une fonction `_run_acme_*_sync()` en tête de fichier (modèle : `_run_legallais_orders_sync`, `_run_prolians_orders_sync`). |

Sans ces changements, le site apparaît mais le lancement tombe dans `_launch_prolians()` ou échoue.

### 4.3 `db/sqlite_db.py` — **base de données**

**Pourquoi :** `init_site_db(site)` refuse les sites absents de `SITE_DB_PATHS`.

**À ajouter :**

```python
SITE_DB_PATHS: dict[str, Path] = {
  ...
  "acme": DIRECTORY / "acme.db",
}
```

- Clé **minuscules**, sans espace (utilisée pour les noms de tables `acme_products`, etc.).
- Aligner avec le slug passé aux appels `init_site_db("acme")`, `insert_product(conn, "acme", row)`, etc. dans les scrapers.

### 4.4 `core/config.py` — **optionnel**

**Pourquoi :** cohérence avec les constantes existantes `SETIN_DB_PATH`, `LEGALLAIS_DB_PATH`, `PROLIANS_DB_PATH`.

**À ajouter (si souhaité) :** `ACME_DB_PATH = DIRECTORY / "acme.db"`.  
Non requis pour l’UI : seul `SITE_DB_PATHS` dans `sqlite_db.py` est utilisé par `init_site_db()`.

### 4.5 `.env.example`

**Pourquoi :** documenter les identifiants attendus par les scrapers du nouveau fournisseur.

**À ajouter :** bloc commenté `User_P{n}` / `Password_P{n}` (même convention que P1, P3, P5).

### 4.6 `auth/__init__.py` — **optionnel**

**Pourquoi :** documentation du package ; pas d’enregistrement automatique.

**À ajouter :** mention du sous-package `auth.acme` dans le docstring, à titre informatif.

### 4.7 Tests — **recommandé**

**Fichier :** `tests/test_sqlite_db.py`  
**Pourquoi :** les tests patchent `SITE_DB_PATHS` pour les trois sites connus ; étendre le fixture si des tests couvrent le nouveau site.

---

## 5. Intégration interface graphique

### 5.1 Ce qui est automatique via `SITES_CONFIG`

| Élément UI | Mécanisme |
|------------|-----------|
| Bouton site | `_build_site_section()` : `for name in SITES_CONFIG` → bouton par clé. |
| Libellé site sélectionné | `_select_site(name)` : `self._site = name`. |
| Boutons d’action | Quatre boutons fixes ; identiques pour tous les sites. |
| Panneau catégories (Produits) | `_select_action("produits")` : si `has_categories`, remplit le `Combobox` avec `categories` ; sinon masque la ligne. |
| Champs dates (Commandes) | Panneau commun ; lus par `_read_dates()` au lancement. |

Aucun autre fichier de configuration GUI (pas de JSON/YAML externe).

### 5.2 Ce qui doit être codé dans `interface.py`

La GUI distingue **trois profils d’intégration** aujourd’hui :

| Site | Produits | Commandes | Suivi | Suppression |
|------|----------|-----------|-------|-------------|
| **Setin** | `import_module` → `create_scraper(category_name=…)` → `_start_async(scraper.run())` | `create_scraper(date_from, date_to)` → async | `create_scraper(date_from, date_to)` → async | `create_scraper()` → async |
| **Prolians** | `create_scraper()` → async | Wrapper `_run_prolians_orders_sync` → `_start_sync` | `create_scraper()` → async | `import_module` → `main()` → sync |
| **Legallais** | Wrapper `_run_legallais_products_sync` → sync | Wrapper `_run_legallais_orders_sync` → sync | `import_module` → `main()` → sync | `cleanup_legallais_addresses` → sync |

**Contrats GUI utiles :**

- **Async (Setin / Prolians produits-suivi) :** module avec `create_scraper(...)` retournant un objet ayant `async def run()` et idéalement `request_stop()` pour le bouton Arrêter.
- **Sync Playwright / Botasaurus :** fonction callable sans `input()` (wrapper GUI) ou `main()` / fonction nommée exposée explicitement (Legallais suppression → `cleanup_legallais_addresses`).

**Export CSV (« Télécharger CSV ») :**

- Mapping nom GUI → slug DB : `_SITE_KEYS` dans `_download()`.
- Tables : `{slug}_products`, `{slug}_orders`, `{slug}_tracking`.
- Mode `suppr` : pas d’export (message informatif).

### 5.3 Chaînage complet : site visible et action lançable

```text
main.py
  └─► ScraperApp (gui/interface.py)
        ├─► SITES_CONFIG  ──► boutons site + imports + catégories
        ├─► _launch()     ──► _launch_<Site>(action)
        │                     └─► import_module(imports[action])
        └─► _download()   ──► init_site_db(_SITE_KEYS[site])
```

Pour qu’un mode soit **disponible** côté utilisateur :

1. Entrée `imports["<mode>"]` valide dans `site_config.py`.
2. Branche correspondante dans `_launch_<Site>()`.
3. Point d’entrée compatible (factory async, `main()`, ou wrapper).
4. Slug SQLite enregistré si le mode persiste en base (produits, commandes, suivi).

---

## 6. Checklist de validation

### Fichiers et emplacement

- [ ] Dossier `scrapers/{Site}_P{n}/` avec les quatre sous-dossiers `products`, `orders`, `tracking`, `deleting`
- [ ] Quatre modules `scrap_*` référencés dans `SITES_CONFIG["imports"]`
- [ ] Modules `scraper_*` associés présents
- [ ] `selectors/{site}.py` créé si `has_categories: True`
- [ ] Package `auth/{site}/` avec gestion de session
- [ ] Variables `User_P{n}` / `Password_P{n}` dans `.env` et documentées dans `.env.example`

### Enregistrement

- [ ] Entrée ajoutée dans `gui/site_config.py` → `SITES_CONFIG`
- [ ] Clé site = libellé affiché dans l’UI (ex. `"Acme"`)
- [ ] Chemins `imports` importables (`python -c "import scrapers.Acme_P7.products.scrap_acme_products"`)

### Persistance

- [ ] Clé ajoutée dans `db/sqlite_db.py` → `SITE_DB_PATHS`
- [ ] Scrapers appellent `init_site_db("<slug>")` avec le même slug minuscule

### Interface graphique

- [ ] `gui/interface.py` : branche `_launch()` pour le nouveau site
- [ ] Méthode `_launch_<Site>()` implémentée pour les quatre actions
- [ ] `_SITE_KEYS` mis à jour pour l’export CSV
- [ ] Wrappers sync ajoutés si les scripts CLI ne sont pas directement appelables depuis un thread

### Vérifications fonctionnelles (manuel)

- [ ] `python main.py` : le site apparaît parmi les boutons « Choisir le site »
- [ ] Sélection du site → les quatre actions sont cliquables
- [ ] Mode **Produits** : combobox catégorie visible ou masquée selon `has_categories`
- [ ] Mode **Commandes** : lancement avec plage de dates sans erreur d’import
- [ ] Mode **Suivi** : lancement OK
- [ ] Mode **Suppression Adresses** : lancement OK
- [ ] Bouton **Télécharger CSV** (produits / commandes / suivi) : export depuis `{slug}_*` après un scrape
- [ ] Session : `manual_login` exécuté une fois si besoin ; `playwright_profiles/{site}/` peuplé

### Hors périmètre de ce document

- Implémentation des sélecteurs CSS, logique de pagination, Botasaurus vs Playwright async
- Contenu des modules `scraper_*.py`

---

## Annexe — Référence rapide des sites existants

| Nom GUI | Dossier scraper | Slug SQLite | Code .env | `has_categories` |
|---------|-----------------|-------------|-----------|------------------|
| Setin | `Setin_P5` | `setin` | P5 | Oui (`Selectors.CATEGORY_NAMES`) |
| Prolians | `Prolians_P3` | `prolians` | P3 | Non |
| Legallais | `Legallais_P1` | `legallais` | P1 | Oui (`CATEGORY_NAMES`) |

**Modules enregistrés dans `SITES_CONFIG` (chemins réels) :**

| Site | produits | commandes | suivi | suppr |
|------|----------|-----------|-------|-------|
| Setin | `scrapers.Setin_P5.products.scrap_setin_products` | `…orders.scrap_setin_orders` | `…tracking.scrap_setin_tracking` | `…deleting.scrap_suppradrr_p5` |
| Prolians | `…Prolians_P3.products.scrap_prolians_products` | `…orders.scrap_prolians_orders` | `…tracking.scrap_prolians_tracking` | `…deleting.scrap_suppradrr` |
| Legallais | `…Legallais_P1.products.scrap_legallais_products` | `…orders.scrap_legallais_orders` | `…tracking.scrap_legallais_tracking` | `…deleting.scrap_legallais_suppradrr` |
