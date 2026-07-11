from enum import Enum
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductKind(str, Enum):
    """Producto simple (vendido tal cual) o configurable (por variantes)."""
    SIMPLE = "SIMPLE"
    CONFIGURABLE = "CONFIGURABLE"


class ProductCreate(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=255,
        description="Nombre del producto.",
        examples=["Helado en copa"],
    )
    description: str | None = Field(
        None, max_length=255,
        description="Descripción opcional del producto.",
    )
    type: ProductKind = Field(
        ProductKind.SIMPLE,
        description="SIMPLE (se vende tal cual) o CONFIGURABLE (por variantes).",
        examples=["CONFIGURABLE"],
    )
    price: Decimal = Field(
        0, ge=0, max_digits=10, decimal_places=2,
        description="Precio base. Solo relevante para SIMPLE; en CONFIGURABLE manda la variante.",
        examples=["8000.00"],
    )
    cost: Decimal = Field(
        0, ge=0, max_digits=10, decimal_places=2,
        description="Costo de referencia del producto.",
        examples=["3000.00"],
    )
    is_menu: bool = Field(False, description="Indica si se muestra en el menú del POS.")
    image_url: str | None = Field(None, max_length=500, description="URL de la imagen.")
    category_id: UUID = Field(..., description="Categoría del producto.")
    unit_measure_id: UUID = Field(..., description="Unidad de medida del producto.")


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=255)
    type: ProductKind | None = None
    is_menu: bool | None = None
    image_url: str | None = Field(None, max_length=500)
    category_id: UUID | None = None
    unit_measure_id: UUID | None = None
    active: bool | None = None


class ProductResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único del producto.")
    name: str = Field(..., description="Nombre del producto.", examples=["Helado en copa"])
    description: str | None = Field(None, description="Descripción del producto.")
    type: ProductKind = Field(..., description="SIMPLE o CONFIGURABLE.", examples=["CONFIGURABLE"])
    is_menu: bool = Field(..., description="Se muestra en el menú.", examples=[True])
    image_url: str | None = Field(None, description="URL de la imagen.")
    category_id: UUID = Field(..., description="Categoría del producto.")
    unit_measure_id: UUID = Field(..., description="Unidad de medida del producto.")
    active: bool = Field(..., description="Indica si el producto está activo.", examples=[True])
    created_at: datetime = Field(..., description="Fecha de creación del registro.")
    updated_at: datetime | None = Field(None, description="Fecha de la última actualización.")

    model_config = ConfigDict(from_attributes=True)


class ProductListResponse(ProductResponse):
    pass


class ProductDetailResponse(ProductResponse):
    pass
