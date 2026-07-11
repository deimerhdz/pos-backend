from enum import Enum
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class OrderScope(str, Enum):
    INDIVIDUAL = "individual"
    TABLE = "table"


class OrderStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class OrderCreate(BaseModel):
    scope: OrderScope = Field(
        ...,
        description=(
            "Alcance de la orden: 'individual' usa solo los productos del comensal actual; "
            "'table' genera una única orden con los productos de toda la mesa."
        ),
        examples=["individual"],
    )


class OrderStatusUpdate(BaseModel):
    status: OrderStatus = Field(
        ...,
        description="Nuevo estado de la orden.",
        examples=["in_progress"],
    )


class OrderItemModifierResponse(BaseModel):
    id: UUID = Field(..., description="Identificador del modificador en la línea.")
    modifier_id: UUID | None = Field(None, description="Modificador de origen.")
    name: str = Field(..., description="Nombre del modificador al momento de la orden.")
    price: Decimal = Field(..., description="Precio del modificador.", examples=["1000.00"])

    model_config = ConfigDict(from_attributes=True)


class OrderItemResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único del item de la orden.")
    variant_id: UUID | None = Field(None, description="Variante pedida.")
    product_id: UUID | None = Field(None, description="Producto pedido.")
    product_name: str = Field(..., description="Nombre del producto al momento de la orden.")
    table_session_id: UUID | None = Field(
        None, description="Sesión del comensal que pidió el item."
    )
    quantity: int = Field(..., description="Cantidad pedida.", examples=[2])
    unit_price: Decimal = Field(..., description="Precio unitario (variante + modificadores).", examples=["2500.00"])
    subtotal: Decimal = Field(..., description="Subtotal del item (unit_price * quantity).", examples=["5000.00"])
    tax_amount: Decimal = Field(..., description="Impuesto de la línea.", examples=["400.00"])
    modifiers: list[OrderItemModifierResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class OrderResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único de la orden.")
    table_id: UUID = Field(..., description="Mesa a la que pertenece la orden.")
    table_session_id: UUID | None = Field(
        None, description="Sesión del comensal (null si es orden de toda la mesa)."
    )
    scope: OrderScope = Field(..., description="Alcance de la orden.")
    customer_name: str | None = Field(None, description="Nombre del comensal (órdenes individuales).")
    status: OrderStatus = Field(..., description="Estado actual de la orden.")
    subtotal: Decimal = Field(..., description="Subtotal pre-impuesto.", examples=["12000.00"])
    tax_total: Decimal = Field(..., description="Impuesto total de la orden.", examples=["960.00"])
    total: Decimal = Field(..., description="Gran total (subtotal + impuestos exclusivos).", examples=["12960.00"])
    items: list[OrderItemResponse] = Field(default_factory=list, description="Items de la orden.")
    created_at: datetime = Field(..., description="Fecha de creación de la orden.")

    model_config = ConfigDict(from_attributes=True)
