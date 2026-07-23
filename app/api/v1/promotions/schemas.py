from enum import Enum
from uuid import UUID
from datetime import datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PromotionType(str, Enum):
    PERCENT = "percent"
    FIXED = "fixed"
    BUY_X_GET_Y = "buy_x_get_y"
    COMBO = "combo"
    QTY_PRICE = "qty_price"


class TargetIn(BaseModel):
    product_id: UUID | None = None
    category_id: UUID | None = None

    @model_validator(mode="after")
    def _one_scope(self):
        if self.product_id is None and self.category_id is None:
            raise ValueError("Cada target requiere product_id o category_id")
        return self


class PromotionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: PromotionType
    value: Decimal = Field(..., ge=0, max_digits=12, decimal_places=2)
    active: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    days_of_week: str | None = Field(None, max_length=20, examples=["0,1,2,3,4"])
    start_time: time | None = None
    end_time: time | None = None
    min_qty: int = Field(1, ge=1)
    buy_qty: int | None = Field(None, ge=1)
    get_qty: int | None = Field(None, ge=1)
    targets: list[TargetIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _percent_range(self):
        if self.type == PromotionType.PERCENT and self.value > 100:
            raise ValueError("Un descuento porcentual no puede superar 100")
        return self


class PromotionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    value: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    active: bool | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    days_of_week: str | None = Field(None, max_length=20)
    start_time: time | None = None
    end_time: time | None = None
    min_qty: int | None = Field(None, ge=1)


class TargetResponse(BaseModel):
    product_id: UUID | None = None
    category_id: UUID | None = None
    model_config = ConfigDict(from_attributes=True)


class PromotionResponse(BaseModel):
    id: UUID
    name: str
    type: str
    value: Decimal
    active: bool
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    days_of_week: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    min_qty: int
    buy_qty: int | None = None
    get_qty: int | None = None
    targets: list[TargetResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)
