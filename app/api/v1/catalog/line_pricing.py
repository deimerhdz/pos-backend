"""Fachada de `app.catalog_engine` (specs/014-extraccion-motor-catalogo,
Historia 3). El motor real vive en `app.catalog_engine`; este módulo
sobrevive solo porque `router.py`/`service.py` de este mismo directorio, y
los siete ficheros consumidores de producción, siguen importando desde esta
ruta — ver contracts/module-api.md Contrato B.

Sin lógica propia de cálculo, validación o consulta (FR-008): cada símbolo
es un reexport nombrado, nunca una función wrapper.
"""
from __future__ import annotations

from app.catalog_engine import (
    _exige_maximo as _exige_maximo,
    check_availability as check_availability,
    compute_line_price as compute_line_price,
    grupos_que_descuentan as grupos_que_descuentan,
    load_valid_options as load_valid_options,
    validate_option_selection as validate_option_selection,
)

# Reexport histórico (FR-009): `cart/service.py:31-36` depende de importar
# estos símbolos desde `line_pricing`, aunque su definición real vive en
# `consumption_plan.py` / `app.catalog_engine`.
from app.catalog_engine import (
    ConsumptionLine as ConsumptionLine,
    load_variant_groups as load_variant_groups,
    plan_line_consumption as plan_line_consumption,
    required_consumption as required_consumption,
)
