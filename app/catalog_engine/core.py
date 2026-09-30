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
    from app.models.product import Product
    from app.models.product_variant import ProductVariant


# Presentación que nombra la variante de un producto sin tamaños reales
# (spec 084, A-79/A-74) -- vive aquí (no en `catalog/service.py`, que sí
# depende del ORM) para que `format_item_description` (spec 087) pueda
# comparar contra ella sin romper la pureza de este módulo (SC-006).
# `catalog/service.py` reexporta este mismo valor para no romper el import
# ya existente en el resto del código.
DEFAULT_PRESENTATION_NAME = "Presentación única"


class ChosenOption(NamedTuple):
    """Una opción elegida junto con cuántas unidades de ella (spec 065). Siempre
    `quantity == 1` para una opción de un grupo "conteo" -- validado en
    `validate_option_selection`, no asumido por el resto del motor."""

    option: "Option"
    quantity: int
    # spec 089 (A-94): `True` = adicional del Menú QR, cobrado y consumido UNA vez por línea
    # (no por unidad de producto). El default `False` es el comportamiento histórico, así
    # que ningún llamador existente (terminal POS, mostrador, checkout, kitchen) cambia.
    # Solo `cart/service.py` la pone en `True`; el resto la LEE de la fila guardada.
    per_line: bool = False


@dataclass(frozen=True)
class ConsumptionLine:
    """Un movimiento de stock a aplicar. `quantity` ya viene multiplicada por la
    cantidad de la línea vendida, y es siempre positiva."""

    inventory_item_id: UUID
    quantity: Decimal
    source: str  # 'receta' | 'variante' | 'opcion' — para diagnóstico y mensajes


def compute_unit_price(variant: ProductVariant, options: Sequence[ChosenOption]) -> Decimal:
    """Precio de UNA unidad de producto (spec 089): precio de la variante + extras de las
    opciones `per_line=False`, cada uno por su cantidad elegida (spec 065). Los adicionales
    `per_line=True` no entran aquí: se cobran una vez por línea (`compute_addons_total`)."""
    price = Decimal(variant.price)
    for chosen in options:
        if not chosen.per_line:
            price += Decimal(chosen.option.extra_price) * chosen.quantity
    return price


def compute_addons_total(options: Sequence[ChosenOption]) -> Decimal:
    """Σ(extra × cantidad elegida) de las opciones `per_line=True` (spec 089): lo que se
    cobra UNA vez por línea, sin importar cuántas unidades del producto se piden."""
    total = Decimal(0)
    for chosen in options:
        if chosen.per_line:
            total += Decimal(chosen.option.extra_price) * chosen.quantity
    return total


def line_total(unit_price: Decimal, quantity: int, addons_total: Decimal | int = 0) -> Decimal:
    """La única fórmula del total de una línea (spec 089): `unit_price × quantity +
    addons_total`. Con `addons_total = 0` (toda línea histórica y la de la terminal POS)
    es el `unit_price × quantity` de siempre."""
    return Decimal(unit_price) * quantity + Decimal(addons_total or 0)


def compute_line_price(variant: ProductVariant, options: Sequence[ChosenOption]) -> Decimal:
    """Snapshot de precio de una línea: precio de la variante + extras de opción,
    cada uno multiplicado por su cantidad elegida (spec 065; siempre 1 en "conteo",
    por lo que esto no cambia el precio de ningún grupo existente).

    spec 089: conserva firma y resultado para todo llamador existente. Es
    `compute_unit_price + compute_addons_total`: con `per_line=False` (todos los
    llamadores salvo el Menú QR) es exactamente lo de siempre."""
    return compute_unit_price(variant, options) + compute_addons_total(options)


def format_item_description(product: Product | None, variant: ProductVariant | None) -> str:
    """Snapshot de texto de una línea vendida/pedida (spec 087, FR-010): nombre
    del producto + presentación, salvo que el producto no maneje presentaciones
    reales (`variant.presentation_name == DEFAULT_PRESENTATION_NAME`) -- ahí se
    omite la etiqueta genérica en vez de mostrar literalmente "Producto -
    Presentación única". Sin `variant` (RN-ORD-32: variante ya borrada), no hay
    nada que describir. Sin `product` (línea huérfana, ya no vive su producto)
    pero con `variant`, se preserva el comportamiento actual: usar solo el
    nombre de la presentación."""
    if variant is None:
        return ""
    if product is None:
        return variant.presentation_name
    if variant.presentation_name == DEFAULT_PRESENTATION_NAME:
        return product.name
    return f"{product.name} - {variant.presentation_name}"


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
