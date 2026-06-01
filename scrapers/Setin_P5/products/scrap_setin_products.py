"""Chef d'orchestre du scraper produits Setin — point d'entrée officiel.

Ce fichier dirige l'ordre d'exécution en appelant les fonctions CSS de scraper_setin_products.py.
Il contient run(), _save_to_db() et expose create_scraper()
pour les consommateurs externes (GUI, tests, cron).
"""

import asyncio
from datetime import datetime

import selectors.setin as SEL
from core.config import DB_PATH, PROFILES_DIR, TIMEOUT_MEDIUM
from core.logger import log_exception
from db.database import init_db
from db.models import SetinOrder, SetinProduct

# Fonctionne à la fois en import package (GUI) et en script standalone (CLI)
try:
    from .scraper_setin_products import SetinProductScraper as _SetinCSS
except ImportError:
    from scraper_setin_products import SetinProductScraper as _SetinCSS  # type: ignore[no-redef]


class SetinProductScraper(_SetinCSS):
    """Chef d'orchestre — orchestre les appels CSS et gère la persistance."""

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Lance le scraping complet de la catégorie sélectionnée."""
        init_db(db_path=DB_PATH, extra_models=[SetinProduct, SetinOrder])

        storage_path = PROFILES_DIR / "setin_storage.json"
        storage_state = str(storage_path) if storage_path.exists() else None

        await self.start_browser(headless=False, storage_state=storage_state)
        page = await self.new_page()

        try:
            await page.goto(SEL.BASE_URL)
            await page.wait_for_load_state("domcontentloaded")

            # Attendre la disparition du loader de page (animation fade-out)
            try:
                await page.locator(SEL.LOGIN["page_loader"]).wait_for(
                    state="hidden", timeout=10000
                )
                self.log.debug("Loader disparu")
            except Exception:
                pass

            # Bouton "retour" de l'interstitiel — toujours présent sur browser frais
            try:
                await page.locator(SEL.LOGIN["home_return_button"]).first.click(
                    timeout=TIMEOUT_MEDIUM
                )
                await page.wait_for_load_state("domcontentloaded")
                self.log.debug("Interstitiel fermé")
            except Exception:
                self.log.debug("Pas d'interstitiel détecté")

            # Ne se reconnecte que si la session est expirée ou absente
            if not await self._is_logged_in(page):
                self.log.info("Session expirée ou absente — reconnexion en cours")
                await self._connexion(page)
                await self.save_storage_state(storage_path)
            else:
                self.log.info("Session active — connexion ignorée")

            cat_urls = await self._get_categories(page, self._category_name)
            self.log.info("Catégories trouvées : %d", len(cat_urls))

            current_index = 1

            for cat_url in cat_urls:
                if self.should_stop():
                    self.log.info("Arrêt demandé — Setin.")
                    break
                try:
                    product_links, cat1, cat2, cat3 = await self._get_products_link(
                        page, cat_url
                    )
                    for link in product_links:
                        if self.should_stop():
                            break
                        try:
                            current_index, products = await self._get_product_data(
                                page, link, current_index
                            )
                            for produit in products:
                                self._save_to_db(produit, cat1, cat2, cat3, link)
                        except Exception as exc:
                            log_exception(self.log, exc, f"Produit {link}")
                except Exception as exc:
                    log_exception(self.log, exc, f"Catégorie {cat_url}")

        except Exception as exc:
            log_exception(self.log, exc, "Erreur fatale run() setin_products")
        finally:
            await self.close()

        self.log.info("Setin terminé — catégorie : %s", self._category_name)

    # ------------------------------------------------------------------
    # Persistance SQLite
    # ------------------------------------------------------------------

    def _save_to_db(
        self,
        produit: dict,
        cat1: str,
        cat2: str,
        cat3: str,
        source_url: str,
    ) -> None:
        """Insère une variante produit dans la table setin_products."""
        SetinProduct.insert(
            scraped_at=datetime.now(),
            source_url=source_url,
            ref_product=produit["ref"],
            ean=produit["ean"],
            ref_fournisseur=produit["four"],
            cat1=cat1,
            cat2=cat2,
            cat3=cat3,
            category_tree=";".join(filter(None, [cat1, cat2, cat3])),
            product_title=produit["title"],
            conditionnement=produit["cdt"],
            product_brand=produit["marque"],
            image_brand=produit["imgMarque"],
            product_image=produit.get("productImages", produit["image"]),
            product_desc=produit["description"],
            product_doc_list=produit["doc"],
            product_attributes=produit["caractéristiques"],
            product_price=produit["prix"],
            eco_tax=produit["eco_tax"],
            reduction=produit["reduc"],
            is_combination=str(produit["IsCombination"]),
            combination_index=produit["IndexCombination"],
            combination_values=produit["combinationValues"],
            parent=produit["parent"],
            produit_lie=",".join(produit["Produit lié"])
            if isinstance(produit["Produit lié"], list)
            else "",
            ref_decli=produit.get("ref_decli", ""),
            stock_status=produit.get("stockStatus", ""),
        ).execute()


def create_scraper(category_name: str = "") -> SetinProductScraper:
    """Fabrique un SetinProductScraper — seul point d'accès officiel pour les consommateurs externes."""
    return SetinProductScraper(category_name=category_name)


async def main(category: str = "") -> None:
    scraper = create_scraper(category_name=category)
    await scraper.run()


if __name__ == "__main__":
    asyncio.run(main())
