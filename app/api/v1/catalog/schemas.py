from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------- Variantes ----------
class VariantCreate(BaseModel):
    # Se recortan los espacios antes de validar: «Pequeña » no es una presentación
    # distinta de «Pequeña», y un nombre de solo espacios queda en 422 por min_length.
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=255, examples=["1 bola", "2 bolas"])
    price: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    sku: str | None = Field(None, max_length=100)
    # spec 040: presentación de catálogo compartido a la que apunta la variante.
    # NULL (por defecto) = no participa de ninguna regla de promoción por
    # presentación (FR-008). Debe existir y estar activa, o ser NULL.
    presentation_id: UUID | None = None


class VariantUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    price: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    sku: str | None = Field(None, max_length=100)
    active: bool | None = None
    # spec 040: enviar `null` desasigna la presentación (usar `model_fields_set`
    # para distinguir "no enviado" de "enviado null").
    presentation_id: UUID | None = None


class VariantResponse(BaseModel):
    id: UUID
    product_id: UUID
    name: str
    sku: str | None = None
    price: Decimal
    active: bool
    presentation_id: UUID | None = None

    model_config = ConfigDict(from_attributes=True)


# Orden de presentaciones (spec 042): el endpoint dedicado de reordenamiento
# (`VariantReorderRequest`/`VariantOrderEntry`/`VariantReorderResponse`) se retiró en spec 043
# (A-55, registro-de-anomalias.md) -- el orden ahora se envía como la posición de cada
# presentación dentro de `variants[]` del guardado consolidado (`VariantSaveIn` abajo).


# ---------- Receta (BOM): insumos fijos ----------
class RecipeItemIn(BaseModel):
    """Un insumo que la variante consume siempre (200 g de fruta). Lo que el cliente
    elige va en `variant_option_groups`, no aquí."""

    inventory_item_id: UUID
    quantity: Decimal = Field(..., gt=0, max_digits=12, decimal_places=3)


class RecipeItemResponse(BaseModel):
    id: UUID
    inventory_item_id: UUID
    quantity: Decimal

    model_config = ConfigDict(from_attributes=True)


# ---------- Grupos de opciones por variante ----------
class VariantOptionGroupIn(BaseModel):
    """Un grupo que ofrece esta variante: cuántas opciones elige el cliente y cuánto
    descuenta **cada una** de ellas.

    `quantity_per_option` es por opción elegida, no el total del grupo: dos sabores con
    120 descuentan 120 de cada uno. En 0 el grupo se ofrece pero no descuenta por sí
    mismo (el consumo, si lo hay, sale de `options.item_quantity`).
    """

    option_group_id: UUID
    min_select: int = Field(0, ge=0)
    max_select: int = Field(1, ge=1)
    quantity_per_option: Decimal = Field(0, ge=0, max_digits=12, decimal_places=3)

    @model_validator(mode="after")
    def _max_ge_min(self) -> "VariantOptionGroupIn":
        if self.max_select < self.min_select:
            raise ValueError("max_select no puede ser menor que min_select")
        return self


class VariantOptionGroupResponse(BaseModel):
    id: UUID
    product_variant_id: UUID
    option_group_id: UUID
    min_select: int
    max_select: int
    quantity_per_option: Decimal

    model_config = ConfigDict(from_attributes=True)


# ---------- Guardado consolidado de producto (spec 043) ----------
class VariantSaveIn(BaseModel):
    """Una presentación dentro del árbol que aceptan `POST`/`PATCH`/`PUT /products` (spec 043).

    `id` distingue crear de actualizar: ausente/`None` crea una presentación nueva; presente
    actualiza la fila existente con ese id (debe pertenecer al producto). Solo tiene sentido en
    `PATCH`/`PUT /products/{id}` -- en `POST /products` el producto todavía no tiene
    presentaciones, así que siempre llega `None` ahí.

    `recipe`/`option_groups` son reemplazo total (mismo patrón que tenían los endpoints ya
    retirados `PUT /variants/{id}/recipe`/`PUT /variants/{id}/option-groups`, A-55). La posición
    de esta entrada dentro de la lista `variants` del body determina su `display_order` (1-based).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    id: UUID | None = None
    name: str = Field(..., min_length=1, max_length=255, examples=["1 bola", "2 bolas"])
    price: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    sku: str | None = Field(None, max_length=100)
    active: bool = True
    recipe: list[RecipeItemIn] = Field(default_factory=list)
    option_groups: list[VariantOptionGroupIn] = Field(default_factory=list)


# ---------- Grupos de opciones ----------
class OptionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["Fresa", "Chocolate"])
    extra_price: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    inventory_item_id: UUID | None = Field(
        None, description="Insumo que descuenta al elegir esta opción."
    )
    item_quantity: Decimal = Field(0, ge=0, max_digits=12, decimal_places=3)


class OptionUpdate(BaseModel):
    """Parcial: solo se aplican los campos presentes en el body. `inventory_item_id`
    en `null` desliga el insumo (usar `model_fields_set` para distinguirlo de ausente)."""

    name: str | None = Field(None, min_length=1, max_length=255)
    extra_price: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    inventory_item_id: UUID | None = None
    item_quantity: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=3)
    active: bool | None = None


class OptionResponse(BaseModel):
    id: UUID
    option_group_id: UUID
    name: str
    extra_price: Decimal
    inventory_item_id: UUID | None = None
    item_quantity: Decimal
    active: bool

    model_config = ConfigDict(from_attributes=True)


class OptionGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["Sabores de helado"])
    min_select: int = Field(0, ge=0)
    max_select: int = Field(1, ge=1)


class OptionGroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    min_select: int | None = Field(None, ge=0)
    max_select: int | None = Field(None, ge=1)
    active: bool | None = None


class OptionGroupResponse(BaseModel):
    id: UUID
    name: str
    min_select: int
    max_select: int
    active: bool
    options: list[OptionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# La asignación grupo<->producto desapareció: los grupos cuelgan de la VARIANTE
# (`VariantOptionGroupIn` arriba), porque cuántas opciones se eligen y cuánto descuenta
# cada una cambian con el tamaño.
