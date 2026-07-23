from uuid import UUID
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class DayBucket(BaseModel):
    day: date
    total: Decimal
    count: int


class SalesReport(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    total_sales: Decimal
    ticket_count: int
    avg_ticket: Decimal
    by_day: list[DayBucket]


class ProductRow(BaseModel):
    product_variant_id: UUID
    description: str
    units: int
    revenue: Decimal


class CategoryRow(BaseModel):
    category_id: UUID | None
    category_name: str | None
    units: int
    revenue: Decimal


class CashierRow(BaseModel):
    user_id: UUID | None
    user_name: str | None
    ticket_count: int
    total: Decimal


class InventoryRow(BaseModel):
    inventory_item_id: UUID
    name: str
    current_stock: Decimal
    min_stock: Decimal
    unit_cost: Decimal
    stock_value: Decimal
    below_min: bool


class ProfitabilityRow(BaseModel):
    category_id: UUID | None
    category_name: str | None
    revenue: Decimal
    cogs: Decimal
    margin: Decimal


class ProfitabilityReport(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    revenue: Decimal
    cogs: Decimal
    margin: Decimal
    by_category: list[ProfitabilityRow]
