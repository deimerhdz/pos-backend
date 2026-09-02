"""Núcleo puro del motor de catálogo: cálculo sin I/O.

Sin imports del ORM ni de nada que dependa de él (ver SC-006 de
specs/014-extraccion-motor-catalogo). `ProductVariant`/`Option` solo se usan
como type hints, bajo `TYPE_CHECKING`, para no arrastrar el ORM en tiempo de
import.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple, Sequence, TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.models.option import Option
    from app.models.product_variant import ProductVariant


class ChosenOption(NamedTuple):
    """Una opción elegida junto con cuántas unidades de ella (spec 065). Siempre
    `quantity == 1` para una opción de un grupo "conteo" -- validado en
    `validate_option_selection`, no asumido por el resto del motor."""

    option: "Option"
    quantity: int


@dataclass(frozen=True)
class ConsumptionLine:
    """Un movimiento de stock a aplicar. `quantity` ya viene multiplicada por la
    cantidad de la línea vendida, y es siempre positiva."""

    inventory_item_id: UUID
    quantity: Decimal
    source: str  # 'receta' | 'variante' | 'opcion' — para diagnóstico y mensajes


def compute_line_price(variant: ProductVariant, options: Sequence[ChosenOption]) -> Decimal:
    """Snapshot de precio de una línea: precio de la variante + extras de opción,
    cada uno multiplicado por su cantidad elegida (spec 065; siempre 1 en "conteo",
    por lo que esto no cambia el precio de ningún grupo existente)."""
    price = Decimal(variant.price)
    for chosen in options:
        price += Decimal(chosen.option.extra_price) * chosen.quantity
    return price


def _exige_maximo(gid: UUID, lo: int, consumen: set[UUID]) -> bool:
    """¿Este grupo obliga a elegir el máximo, y no solo el mínimo?

    Solo si descuenta inventario **y** es obligatorio. Un grupo así reparte una
    cantidad física fija entre las opciones elegidas: los tres sabores de un
    helado de tres bolas. Elegir uno solo sirve tres bolas y descuenta una.

    Un grupo que descuenta pero es opcional (`min_select = 0`) se queda como
    está: ahí no elegir es una respuesta válida y el consumo cuadra con lo que
    se sirve.
    """
    return lo > 0 and gid in consumen
