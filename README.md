# Gestionnaire de Scrapers

Interface graphique simple pour lancer les scrapers et visualiser les donnees.

## Demarrage

```bash
uv run main.py
```

L'interface s'ouvrira automatiquement dans une fenetre.

## Fonctionnalites

### Onglet "Scraper"
- Lancez le scraper Setin Orders
- Selectionnez la plage de dates (debut et fin)
- Visualisez la progression en temps reel dans le journal
- Arretez le scraper a tout moment

### Onglet "Donnees"
- Visualisez toutes les commandes scrapees
- Consultez les details: ID, Reference, Date, Statut, Transporteur, Numero de suivi
- Rafraichissez les donnees pour voir les changements
- Exportez les donnees en format CSV

## Comment utiliser

### Scraper les donnees

1. Lancez l'application: `uv run main.py`
2. Allez dans l'onglet **"Scraper"**
3. Definissez la plage de dates:
   - **Date de debut**: YYYY-MM-DD (ex: 2026-05-20)
   - **Date de fin**: YYYY-MM-DD (ex: 2026-05-27)
4. Cliquez sur **"Lancer Setin Orders"**
5. Observez la progression dans le journal
6. Les commandes seront automatiquement sauvegardees en base de donnees

### Visualiser les donnees

1. Allez dans l'onglet **"Donnees"**
2. Les commandes s'affichent dans le tableau
3. Cliquez sur **"Rafraichir"** pour mettre a jour
4. Cliquez sur **"Exporter en CSV"** pour telecharger les donnees

## Configuration requise

Votre fichier `.env` doit contenir:
```
User_P5=votre_identifiant
Password_P5=votre_mot_de_passe
```

## Base de donnees

Les donnees sont stockees dans SQLite:
- Fichier: `setin_data.db`
- Table: `setin_orders`
- Colonnes: ID, Reference, Date, Statut, Produit, Transporteur, Suivi

## Depannage

**Erreur d'authentification:**
- Verifiez vos identifiants P5 dans `.env`

**Pas de donnees affichees:**
- Verifiez votre connexion internet
- Verifiez que votre compte Setin est actif
- Cliquez sur "Rafraichir"

**Le scraper est lent:**
- C'est normal! Le scraper doit charger chaque page
- Le nombre de pages depend de la plage de dates
