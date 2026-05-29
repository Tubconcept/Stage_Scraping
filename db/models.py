"""Modèles ORM Peewee pour la base de données Setin."""

from datetime import datetime
from peewee import (
    SqliteDatabase,
    Model,
    CharField,
    TextField,
    DateTimeField,
)

# Sera initialisée par init_db()
db = SqliteDatabase(":memory:")


class BaseModel(Model):
    """Classe de base pour tous les modèles."""

    class Meta:
        database = db


class SetinProduct(BaseModel):
    """Modèle pour les produits Setin extraits."""

    ref_product = CharField(help_text="Référence produit")
    source_url = CharField(null=True, help_text="URL de la fiche produit")
    ean = CharField(null=True, help_text="Code EAN")
    ref_fournisseur = CharField(null=True, help_text="Référence fournisseur")
    cat1 = CharField(null=True, help_text="Catégorie niveau 1")
    cat2 = CharField(null=True, help_text="Catégorie niveau 2")
    cat3 = CharField(null=True, help_text="Catégorie niveau 3")
    product_title = TextField(null=True, help_text="Titre du produit")
    conditionnement = CharField(null=True, help_text="Conditionnement")
    product_brand = CharField(null=True, help_text="Marque")
    image_brand = CharField(null=True, help_text="Image marque")
    product_image = TextField(null=True, help_text="URLs images produit (URL1, URL2, ...)")
    product_desc = TextField(null=True, help_text="Description")
    product_doc_list = TextField(null=True, help_text="Documentation / liens")
    product_attributes = TextField(null=True, help_text="Caractéristiques")
    product_price = CharField(null=True, help_text="Prix")
    eco_tax = CharField(null=True, help_text="Éco taxe")
    reduction = CharField(null=True, help_text="Réduction")
    is_combination = CharField(null=True, help_text="Est combinaison")
    combination_index = CharField(null=True, help_text="Index combinaison")
    combination_values = CharField(null=True, help_text="Valeurs combinaison")
    parent = CharField(null=True, help_text="Produit parent")
    produit_lie = TextField(null=True, help_text="Produits liés")
    ref_decli = CharField(null=True, help_text="Refs variantes liées (ref1|ref2|...)")
    stock_status = CharField(null=True, help_text="Statut stock (non extrait — aucun sélecteur disponible)")
    category_tree = CharField(null=True, help_text="Arborescence catégories (cat1;cat2;cat3)")
    scraped_at = DateTimeField(default=datetime.now, help_text="Date scraping")

    @property
    def product_ref(self) -> str | None:
        return self.ref_product

    class Meta:
        table_name = "setin_products"


class SetinOrder(BaseModel):
    """Commandes Setin — 5 champs métier."""

    id_cmd = CharField(help_text="Référence P5 (ex: B12345ABC)")
    ref_cmd = CharField(null=True, help_text="Référence interne Setin")
    date_cmd = CharField(null=True, help_text="Date commande (DD/MM/YYYY)")
    statut_cmd = CharField(null=True, help_text="Statut de la commande")
    data_pdt = TextField(null=True, help_text="Données produit (ref:titre:qty)")
    scraped_at = DateTimeField(default=datetime.now, help_text="Date scraping")

    class Meta:
        table_name = "setin_orders"


class SetinTracking(BaseModel):
    """Suivi des expéditions Setin — 7 derniers jours."""

    id_cmd = CharField(help_text="Référence P5")
    ref_cmd = CharField(null=True, help_text="Référence interne Setin")
    date_cmd = CharField(null=True, help_text="Date commande (DD/MM/YYYY)")
    statut_cmd = CharField(null=True, help_text="Statut de la commande")
    data_pdt = TextField(null=True, help_text="Données produit (ref:titre:qty)")
    Date_Reliquat = CharField(null=True, help_text="Date reliquat (DD/MM/YYYY)")
    weight_exp = CharField(null=True, help_text="Poids expédition")
    carrier_exp = CharField(null=True, help_text="Transporteur détecté")
    trackinglink_exp = CharField(null=True, help_text="URL de suivi transporteur")
    tracking_exp = CharField(null=True, help_text="Numéro de suivi")
    scraped_at = DateTimeField(default=datetime.now, help_text="Date scraping")

    class Meta:
        table_name = "setin_tracking"
