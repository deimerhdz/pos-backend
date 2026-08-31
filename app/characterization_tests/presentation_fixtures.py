"""Infraestructura compartida para los characterization tests de la spec 040
(`test_presentations_service.py`, `test_promotions_presentation_rules.py`,
`test_promotions_presentation_pricing.py`) — no es código de producción.

Es una fachada delgada sobre `orders_fixtures.py` (superconjunto de catálogo +
mesas/pedidos/carrito/promociones/caja/ventas/factura, ya con las dos tablas
nuevas de la spec 040 y sus factories). No se toca ningún fichero de producción.
"""
from __future__ import annotations

from app.characterization_tests.orders_fixtures import (  # noqa: F401  reexport
    new_session,
    make_category,
    make_inventory_item,
    make_option,
    make_option_group,
    make_product,
    make_recipe_item,
    make_unit,
    make_variant,
    link_variant_group,
    make_dining_table,
    make_table_session,
    make_participant,
    make_customer_order,
    make_order_item,
    make_cart,
    make_cart_item,
    make_promotion,
    make_promotion_target,
    make_combo_item,
    make_presentation,
    make_presentation_rule,
    assign_presentation,
    make_cash_register,
    make_cash_shift,
    make_payment_method,
    make_payment_attempt,
    make_tenant_double,
    make_user_double,
)

__all__ = [
    "new_session",
    "make_category", "make_inventory_item", "make_option", "make_option_group",
    "make_product", "make_recipe_item", "make_unit", "make_variant",
    "link_variant_group",
    "make_dining_table", "make_table_session", "make_participant",
    "make_customer_order", "make_order_item", "make_cart", "make_cart_item",
    "make_promotion", "make_promotion_target", "make_combo_item",
    "make_presentation", "make_presentation_rule", "assign_presentation",
    "make_cash_register", "make_cash_shift", "make_payment_method",
    "make_payment_attempt", "make_tenant_double", "make_user_double",
]
