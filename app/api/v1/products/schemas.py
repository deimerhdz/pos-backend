from enum import Enum
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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


class ProductUpdate(BaseModel):
    category_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=500)
    preparation_type: PreparationType | None = None
    image_url: str | None = Field(None, max_length=500)
    active: bool | None = None


class ProductResponse(BaseModel):
    id: UUID
    category_id: UUID
    name: str
    description: str | None = None
    preparation_type: PreparationType
    image_url: str | None = None
    active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ProductListResponse(ProductResponse):
    pass


class ProductDetailResponse(ProductResponse):
    pass
