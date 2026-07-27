from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class MenuOptionResponse(BaseModel):
    id: UUID
    name: str
    extra_price: Decimal

    model_config = ConfigDict(from_attributes=True)


class MenuOptionGroupResponse(BaseModel):
    id: UUID
    name: str
    min_select: int
    max_select: int
    options: list[MenuOptionResponse] = Field(default_factory=list)


class MenuVariantResponse(BaseModel):
    id: UUID
    name: str
    price: Decimal

    model_config = ConfigDict(from_attributes=True)


class MenuProductResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    image_url: str | None = None
    variants: list[MenuVariantResponse] = Field(default_factory=list)
    option_groups: list[MenuOptionGroupResponse] = Field(default_factory=list)


class MenuCategoryResponse(BaseModel):
    id: UUID
    name: str
    products: list[MenuProductResponse] = Field(default_factory=list)


class MenuTableResponse(BaseModel):
    id: UUID
    number: int
    name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class MenuBusinessResponse(BaseModel):
    """Branding del negocio para el menú público del QR.

    El comensal es anónimo y no puede llamar a `GET /tenant` (requiere auth), así
    que el nombre y el logo del negocio viajan dentro de la respuesta del menú.
    """

    name: str
    logo_url: str | None = None

    model_config = ConfigDict(from_attributes=True)
