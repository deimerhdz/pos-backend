from enum import Enum
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.catalog.schemas import (
    VariantSaveIn,
    VariantResponse,
    RecipeItemResponse,
    VariantOptionGroupResponse,
)


class PreparationType(str, Enum):
    """'prepared' = se arma con receta; 'packaged' = se vende empacado."""
    PREPARED = "prepared"
    PACKAGED = "packaged"


class ProductCreate(BaseModel):
    category_id: UUID = Field(..., description="Categoría del producto.")
    name: str = Field(..., min_length=1, max_length=255, examples=["Helado en copa"])
    description: str | None = Field(None, max_length=500)
    preparation_type: PreparationType = Field(
        PreparationType.PREPARED,
        description="prepared (receta) o packaged (empacado).",
    )
    image_url: str | None = Field(None, max_length=500)
    available: bool = True
    tracks_inventory: bool = Field(
        False, description="Si el producto exige y aplica descuento de inventario en sus presentaciones."
    )
    variants: list[VariantSaveIn] = Field(
        default_factory=list,
        description=(
            "Presentaciones iniciales del producto, con su receta y grupos de opciones (spec "
            "043). Si viene vacía, se preserva el comportamiento actual: se crea automáticamente "
            "la presentación 'Single' a precio 0 (RN-CAT-05)."
        ),
    )


class ProductUpdate(BaseModel):
    category_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=500)
    preparation_type: PreparationType | None = None
    image_url: str | None = Field(None, max_length=500)
    active: bool | None = None
    available: bool | None = None
    tracks_inventory: bool | None = None
    variants: list[VariantSaveIn] | None = Field(
        None,
        description=(
            "Árbol completo de presentaciones deseado (spec 043). Ausente = no tocar ninguna "
            "presentación (back-compat). Presente (incluida lista vacía) = reemplazo total: crea "
            "las entradas sin `id`, actualiza las que traen `id`, desactiva cualquier "
            "presentación activa no listada. Distinguir 'ausente' de '[]' requiere leer "
            "`model_fields_set`, no solo `is None` (ver ProductService.update_product)."
        ),
    )


class ProductResponse(BaseModel):
    id: UUID
    category_id: UUID
    name: str
    description: str | None = None
    preparation_type: PreparationType
    image_url: str | None = None
    active: bool
    available: bool
    tracks_inventory: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ProductListResponse(ProductResponse):
    pass


class ProductDetailResponse(ProductResponse):
    pass


class VariantSaveOut(VariantResponse):
    """Estado final de una presentación tras un guardado consolidado (spec 043).

    Extiende `VariantResponse` (`id`, `product_id`, `name`, `sku`, `price`, `active`); `recipe` y
    `option_groups` no se pueden poblar por `from_attributes` porque el modelo ORM los expone como
    `recipe_items`/`option_groups` con otro shape -- el servicio los arma explícitamente al
    construir la respuesta.
    """

    display_order: int
    recipe: list[RecipeItemResponse] = Field(default_factory=list)
    option_groups: list[VariantOptionGroupResponse] = Field(default_factory=list)


class ProductSaveResponse(ProductResponse):
    """Respuesta de `POST`/`PATCH`/`PUT /products` (spec 043, FR-006): el árbol completo y final
    del producto guardado, para que el formulario no necesite una lectura adicional."""

    variants: list[VariantSaveOut] = Field(default_factory=list)
