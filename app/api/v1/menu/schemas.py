from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class MenuSessionCreate(BaseModel):
    qr_code: str = Field(
        ..., min_length=1, max_length=255,
        description="Código QR de la mesa escaneada.",
        examples=["QR-MESA-1"],
    )
    customer_name: str = Field(
        ..., min_length=1, max_length=255,
        description="Nombre del comensal que abre la sesión.",
        examples=["Juan"],
    )


class MenuSessionResponse(BaseModel):
    session_token: str = Field(..., description="Token de la sesión. Enviar en el header 'X-Menu-Session'.")
    customer_name: str = Field(..., description="Nombre del comensal.")
    table_id: UUID = Field(..., description="Identificador de la mesa.")
    table_name: str = Field(..., description="Nombre de la mesa.", examples=["Mesa 1"])
    capacity: int = Field(..., description="Capacidad de la mesa.", examples=[4])


class MenuCategoryResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único de la categoría.")
    name: str = Field(..., description="Nombre de la categoría.", examples=["Bebidas"])
    description: str | None = Field(None, description="Descripción de la categoría.")

    model_config = ConfigDict(from_attributes=True)


class MenuProductResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único del producto.")
    name: str = Field(..., description="Nombre del producto.", examples=["Helado en copa"])
    description: str | None = Field(None, description="Descripción del producto.")
    type: str = Field(..., description="SIMPLE o CONFIGURABLE.", examples=["CONFIGURABLE"])
    category_id: UUID = Field(..., description="Categoría a la que pertenece el producto.")
    is_available: bool = Field(True, description="Disponibilidad real calculada (stock vigente − reservas).")

    model_config = ConfigDict(from_attributes=True)


# --- variantes y modificadores del producto (para armar la selección en el menú) ---
class MenuVariantResponse(BaseModel):
    id: UUID
    sku: str
    price: Decimal
    values: list[str] = Field(default_factory=list, description="Valores de la combinación.")


class MenuModifierResponse(BaseModel):
    id: UUID
    name: str
    price: Decimal


class MenuModifierGroupResponse(BaseModel):
    id: UUID
    name: str
    required: bool
    min_select: int
    max_select: int | None = None
    modifiers: list[MenuModifierResponse] = Field(default_factory=list)


class MenuProductVariantsResponse(BaseModel):
    product_id: UUID
    type: str
    variants: list[MenuVariantResponse] = Field(default_factory=list)
    modifier_groups: list[MenuModifierGroupResponse] = Field(default_factory=list)


# --- carrito ---
class CartItemCreate(BaseModel):
    variant_id: UUID = Field(..., description="Variante a agregar al carrito.")
    quantity: int = Field(1, ge=1, description="Cantidad.", examples=[1])
    modifier_ids: list[UUID] = Field(
        default_factory=list, description="Modificadores elegidos (toppings/salsas)."
    )


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=1, description="Nueva cantidad del item.", examples=[2])


class CartModifierLine(BaseModel):
    modifier_id: UUID | None = None
    name: str
    price: Decimal


class CartItemResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único del item del carrito.")
    variant_id: UUID | None = Field(None, description="Variante agregada.")
    product_id: UUID | None = Field(None, description="Producto de la variante.")
    product_name: str = Field(..., description="Nombre del producto.")
    variant_sku: str | None = Field(None, description="SKU de la variante.")
    quantity: int = Field(..., description="Cantidad.", examples=[2])
    unit_price: Decimal = Field(..., description="Precio unitario (variante + modificadores).", examples=["10500.00"])
    subtotal: Decimal = Field(..., description="Subtotal (unit_price * quantity).", examples=["21000.00"])
    modifiers: list[CartModifierLine] = Field(default_factory=list)
    table_session_id: UUID = Field(..., description="Sesión del comensal que agregó el item.")
    customer_name: str = Field(..., description="Nombre del comensal que agregó el item.")
    is_mine: bool = Field(..., description="Indica si el item lo agregó la sesión actual.")


class CartResponse(BaseModel):
    table_id: UUID = Field(..., description="Mesa a la que pertenece el carrito.")
    items: list[CartItemResponse] = Field(default_factory=list, description="Items del carrito de la mesa.")
    total: Decimal = Field(..., description="Total del carrito de la mesa.", examples=["21000.00"])
