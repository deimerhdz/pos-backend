"""Conversión de cantidades entre unidades de la misma dimensión.

Usa `UnitMeasure.dimension` + `factor_to_base` (Fase 1): el factor lleva la unidad
a la base de su dimensión (ej. kg→1000 g, l→1000 ml). Convertir entre dos unidades
de la misma dimensión es: qty * from.factor_to_base / to.factor_to_base.
"""
from decimal import Decimal

from fastapi import HTTPException, status

from app.models.unit_measure import UnitMeasure


def convert(quantity: Decimal, from_unit: UnitMeasure, to_unit: UnitMeasure) -> Decimal:
    """Convierte `quantity` de `from_unit` a `to_unit`. Lanza 422 si las
    dimensiones no coinciden (p. ej. no se puede pasar de g a ml)."""
    if from_unit.dimension != to_unit.dimension:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unidades incompatibles: {from_unit.abbreviation} ({from_unit.dimension}) "
                f"vs {to_unit.abbreviation} ({to_unit.dimension})."
            ),
        )
    return (
        Decimal(quantity)
        * Decimal(from_unit.factor_to_base)
        / Decimal(to_unit.factor_to_base)
    )
