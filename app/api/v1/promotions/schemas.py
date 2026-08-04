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


class ComboItemIn(BaseModel):
    product_variant_id: UUID
    quantity: int = Field(1, ge=1)


class PromotionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: PromotionType
    value: Decimal = Field(..., ge=0, max_digits=12, decimal_places=2)
    active: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    days_of_week: str | None = Field(None, max_length=20, examples=["0,1,2,3,4"])
    days_of_month: str | None = Field(None, max_length=100, examples=["15,30"])
    start_time: time | None = None
    end_time: time | None = None
    min_qty: int = Field(1, ge=1)
    buy_qty: int | None = Field(None, ge=1)
    get_qty: int | None = Field(None, ge=1)
    targets: list[TargetIn] = Field(default_factory=list)
    combo_items: list[ComboItemIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _percent_range(self):
        if self.type == PromotionType.PERCENT and self.value > 100:
            raise ValueError("Un descuento porcentual no puede superar 100")
        return self

    @model_validator(mode="after")
    def _combo_items_required(self):
        if self.type == PromotionType.COMBO:
            variant_ids = {it.product_variant_id for it in self.combo_items}
            if len(variant_ids) < 2:
                raise ValueError("Un combo requiere al menos 2 productos distintos en combo_items")
            if len(variant_ids) != len(self.combo_items):
                raise ValueError("combo_items no puede repetir product_variant_id")
        elif self.combo_items:
            raise ValueError("combo_items solo aplica a promociones type=combo")
        return self


class PromotionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    value: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    active: bool | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    days_of_week: str | None = Field(None, max_length=20)
    days_of_month: str | None = Field(None, max_length=100)
    start_time: time | None = None
    end_time: time | None = None
    min_qty: int | None = Field(None, ge=1)


class TargetResponse(BaseModel):
    product_id: UUID | None = None
    category_id: UUID | None = None
    model_config = ConfigDict(from_attributes=True)


class ComboItemResponse(BaseModel):
    product_variant_id: UUID
    quantity: int
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
    days_of_month: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    min_qty: int
    buy_qty: int | None = None
    get_qty: int | None = None
    targets: list[TargetResponse] = Field(default_factory=list)
    combo_items: list[ComboItemResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)
