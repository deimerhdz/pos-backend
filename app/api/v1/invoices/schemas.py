from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class InvoiceItemResponse(BaseModel):
    product_variant_id: UUID
    description: str
    options: list = Field(default_factory=list)
    quantity: int
    unit_price: Decimal
    line_total: Decimal

    model_config = ConfigDict(from_attributes=True)


class InvoiceResponse(BaseModel):
    id: UUID
    sale_id: UUID
    customer_order_id: UUID | None = None
    prefix: str
    number: int
    customer_name: str | None = None
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    tip: Decimal
    total: Decimal
    status: str
    issued_at: datetime
    user_name: str | None = None
    cufe: str | None = None
    dian_status: str | None = None
    items: list[InvoiceItemResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def full_number(self) -> str:
        return f"{self.prefix}{self.number:06d}"
