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
    name: str = Field(..., description="Nombre del producto.", examples=["Coca-Cola 350ml"])
    description: str | None = Field(None, description="Descripción del producto.")
    price: Decimal = Field(..., description="Precio de venta del producto.", examples=["2500.00"])
    category_id: UUID = Field(..., description="Categoría a la que pertenece el producto.")

    model_config = ConfigDict(from_attributes=True)


class CartItemCreate(BaseModel):
    product_id: UUID = Field(..., description="Producto a agregar al carrito.")
    quantity: int = Field(
        1, ge=1, description="Cantidad a agregar. Si el producto ya está, se suma.", examples=[1]
    )


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=1, description="Nueva cantidad del item.", examples=[2])


class CartItemResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único del item del carrito.")
    product_id: UUID = Field(..., description="Producto agregado.")
    product_name: str = Field(..., description="Nombre del producto.")
    quantity: int = Field(..., description="Cantidad.", examples=[2])
    unit_price: Decimal = Field(..., description="Precio unitario actual del producto.", examples=["2500.00"])
    subtotal: Decimal = Field(..., description="Subtotal (unit_price * quantity).", examples=["5000.00"])
    table_session_id: UUID = Field(..., description="Sesión del comensal que agregó el item.")
    customer_name: str = Field(..., description="Nombre del comensal que agregó el item.")
    is_mine: bool = Field(..., description="Indica si el item lo agregó la sesión actual.")


class CartResponse(BaseModel):
    table_id: UUID = Field(..., description="Mesa a la que pertenece el carrito.")
    items: list[CartItemResponse] = Field(default_factory=list, description="Items del carrito de la mesa.")
    total: Decimal = Field(..., description="Total del carrito de la mesa.", examples=["15000.00"])
