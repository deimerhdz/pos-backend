"""Fachada de `app.catalog_engine` (specs/014-extraccion-motor-catalogo,
Historia 3). El motor real vive en `app.catalog_engine`; este módulo
sobrevive solo porque los ficheros consumidores de producción siguen
importando desde esta ruta — ver contracts/module-api.md Contrato B.

Sin lógica propia de cálculo, validación o consulta (FR-008): cada símbolo
es un reexport nombrado, nunca una función wrapper.
"""
from __future__ import annotations

from app.catalog_engine import (
    ConsumptionLine as ConsumptionLine,
    ensure_lines_consume_inventory as ensure_lines_consume_inventory,
    group_discounts as group_discounts,
    load_recipe as load_recipe,
    load_variant_groups as load_variant_groups,
    plan_line_consumption as plan_line_consumption,
    required_consumption as required_consumption,
    variant_label as variant_label,
)
