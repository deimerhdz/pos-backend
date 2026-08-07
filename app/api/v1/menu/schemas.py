from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.promotions.schemas import PromotionType


class MenuOptionResponse(BaseModel):
    id: UUID
    name: str
    extra_price: Decimal
    # Hay stock del insumo que consume esta opción. Solo el booleano: el comensal es
    # anónimo y no debe ver ni los insumos ni las existencias del negocio.
    available: bool = True

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
    # Precio ya con el mejor descuento percent/fixed vigente aplicado, o `None` si
    # ninguna promoción aplica. Se evalúa asumiendo cantidad 1 (aún no hay carrito).
    discounted_price: Decimal | None = None
    # Tipo de la promoción que generó `discounted_price` ("percent"/"fixed"), o
    # `None` si no hay descuento — el cliente lo usa solo para elegir cómo mostrar
    # la insignia de descuento (% vs. monto fijo), nunca para recalcular precios.
    discount_kind: PromotionType | None = None
    # Los grupos cuelgan de la presentación: cuántas opciones se eligen cambia con el
    # tamaño (la ensalada pequeña 1 sabor, la mediana 2). Esta es la fuente autoritativa.
    option_groups: list[MenuOptionGroupResponse] = Field(default_factory=list)
    available: bool = True

    model_config = ConfigDict(from_attributes=True)


class MenuProductResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    image_url: str | None = None
    variants: list[MenuVariantResponse] = Field(default_factory=list)
    # Unión de los grupos de todas las presentaciones. **Solo sirve para resolver
    # nombres y precios de una opción** (tickets, comandas, carrito): su `min/max_select`
    # es el de la primera presentación que lo ofrece y no significa nada. Para saber
    # qué puede elegir el cliente hay que mirar `variants[].option_groups`.
    option_groups: list[MenuOptionGroupResponse] = Field(default_factory=list)
    # False si ninguna presentación se puede pedir (todas tienen algún grupo
    # obligatorio sin opciones con stock).
    available: bool = True


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
