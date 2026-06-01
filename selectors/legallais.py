"""Sélecteurs CSS/Playwright pour le site Legallais (www.legallais.com)."""

BASE_URL: str = "https://www.legallais.com/"

LOGIN: dict[str, str] = {
    "email_input": "input[name='connexion[login]'], input[type='text'], #connection-id",
    "password_input": "input[name='connexion[password]'], input[type='password'], #connection-passwd",
    "login_button": "button[data-action='components--connection#sendConnection']",
}

NAVIGATION: dict[str, str] = {
    "breadcrumb": "ol.c-breadcrumb",
    "menu_products": ".o-menu__products-items",
    "menu_level1": ".o-menu__level--1 .o-menu__items__list > li > a span.c-menu-item__text",
    "menu_level2": "div.o-menu__level--2 > div.o-menu__items--active ul.o-menu__items__list li > a",
    "menu_level3": ".o-menu__level--3 > .o-menu__items--active > .o-menu__items__list li.o-menu__item a.c-menu-item",
}

PRODUCTS: dict[str, str] = {
    "product_fournisseur": "",
    "product_reference_fournisseur": "div.c-reference",
    "product_ean": "span.code_ean span.code_ean_value",
    "product_reference_fabricant": "",
    "product_brand": "",
    "product_brand_logo_url": "a.u-display-contents img",
    "product_designation": "div.product-title > h1 > span",
    "product_description": "section.description",
    "product_image_url": "div.c-photo-gallery__thumbnail-item img",
    "product_docs_url": "div.o-layout.u-padding-horizontal a",
    "product_category_tree": "ol.c-breadcrumb-legacy li.c-breadcrumb-legacy__item",
    "product_conditionnement": "",
    "product_stock_status": "div.c-stock > div.c-stock__label",
    "product_status": "",
    "product_fournisseur_url": "a.c-article-card__product-info",
    "product_eco_label": "",
    "product_eco_taxe": "div.c-price.c-price--rep div.c-price__price",
    "product_promotion": ".c-price.c-price--original",
    "product_price_ht": "div.c-price.c-price--final",
    "product_attributes": "tr#characCodeProvider td",
    "products_is_combination": "",
    "product_combination_index": "",
    "product_parent_reference": "",
    "product_child_reference": "",
    "product_combination_values": "table#DataTables_Table_0 > tbody > tr",
    "product_cross_sell": "",
}

PRODUCTS_COLLECTION: dict[str, str] = {
    "search_input": "input[type='search']",
    "search_result": "div.c-article-search-card a.c-article-search-card__hover",
    "ref_result": "div.c-article-search-card div.c-article-search-card__inner div.c-article-search-card__info p.c-article-search-card__ref span.c-article-search-card__ref__value",
    "product_card": ".c-article-card.c-article-card--incontainer",
    "product_link": "a.c-article-card__product-info",
    "pagination_select": "select.c-pagination__selector",
    "pagination_items_per_page": "select.c-pagination__nbrpp__select",
}

PRODUCT_DETAIL: dict[str, str] = {
    "price_final": "div.c-price.c-price--final",
    "price_original": ".c-price.c-price--original",
    "price_eco": "div.c-price.c-price--rep div.c-price__price",
    "stock": "div.c-stock > div.c-stock__label",
    "brand_image": "a.u-display-contents img",
    "product_images": "div.c-photo-gallery__thumbnail-item img",
    "description": "section.description",
    "documents": "div.o-layout.u-padding-horizontal a",
    "characteristics_table": "#characteristicsTable > tbody > tr",
    "characteristics_ref": "tr#characCodeProvider td",
    "combinations_table": "table#DataTables_Table_0 > tbody > tr",
    "combinations_headers": "table#DataTables_Table_0 thead th",
    "ean_value": "span.code_ean span.code_ean_value",
    "supplier_ref": "span.ref_fournisseur span.ref_fournisseur_value",
}

ORDERS: dict[str, str] = {
    "id": "",
    "id_cmd": "",
    "ref_cmd": "table#DataTables_Table_0 tbody tr",
    "date_cmd": "td.sorting_1",
    "statut_cmd": "span.c-tag",
    "data_pdt": "div.order-details__parcel-designation > div.order-details__parcel-designation-ref",
}

ORDERS_DETAIL: dict[str, str] = {
    "order_header_lines": "div.o-wrapper > div.user-content div.order-details__heading > div.pro-space-order-details__container div.pro-space-order-details__line",
    "order_date_text": "p.order-details__heading-date span.u-bold",
    "product_link": "div.order-details__parcel-designation > a.order-details__parcel-designation-link",
    "product_reference": "div.order-details__parcel-designation > div.order-details__parcel-designation-ref",
    "product_quantity": "div.order-details__parcel-quantity-number",
    "reliquat_product_block": "div.order-details__parcel-designation",
    "reliquat_product_link": "a.order-details__parcel-designation-link",
    "reliquat_stock_label": "div.c-stock__label",
    "multi_product_container": "div.c-card div.o-layout__item",
    "multi_product_item": "li.order-details__parcel-item",
}

TRACKING: dict[str, str] = {
    "id": "",
    "id_cmd": "",
    "ref_cmd": "",
    "date_cmd": "td.sorting_1",
    "statut_cmd": "span.c-tag",
    "data_pdt": "",
    "Date_Reliquat": "div.c-stock__label",
    "weight_exp": "table tbody tr.order-details__modal-tracking-info-content td",
    "carrier_exp": "table tbody tr.order-details__modal-tracking-info-content td",
    "trackinglink_exp": "a",
    "tracking_exp": "table tbody tr.order-details__modal-tracking-info-content td",
}

TRACKING_MODAL: dict[str, str] = {
    "tracking_modal_button": "a.modal-link",
    "tracking_modal": ".mfp-content",
    "tracking_table_cells": "table tbody tr.order-details__modal-tracking-info-content td",
    "tracking_link": "a",
}
