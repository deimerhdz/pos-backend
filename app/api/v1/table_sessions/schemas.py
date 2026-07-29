from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.sales.schemas import PaymentIn


class BillingMode(str, Enum):
    """Cómo se divide la cuenta al cerrar. Lo elige el cajero al cobrar, no el
    cliente."""
    #: Una sola venta con todo lo consumido en la mesa.
    UNIFIED = "unified"
    #: Una venta por comensal, agrupando por `order_items.participant_id`.
    SPLIT = "split"


class ParticipantResponse(BaseModel):
    id: UUID
    display_name: str
    display_label: str | None = None
    status: str
    joined_at: datetime
    expires_at: datetime | None = None
    closed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TableSessionResponse(BaseModel):
    id: UUID
    dining_table_id: UUID
    status: str
    opened_at: datetime
    closed_at: datetime | None = None
    closed_by_user_name: str | None = None
    billing_mode: str | None = None
    participants: list[ParticipantResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class SessionBillLine(BaseModel):
    """Lo que debe un comensal. `participant_id` nulo agrupa lo que añadió el
    staff sin asignar a nadie."""
    participant_id: UUID | None = None
    display_label: str | None = None
    subtotal: Decimal


class SessionBillResponse(BaseModel):
    table_session_id: UUID
    dining_table_id: UUID
    total: Decimal
    order_ids: list[UUID] = Field(default_factory=list)
    split: list[SessionBillLine] = Field(default_factory=list)


class SplitPaymentIn(BaseModel):
    """Pago de un comensal concreto en el modo `split`."""
    participant_id: UUID | None = Field(
        None,
        description="Null para cobrar los ítems sin comensal asignado "
                    "(los que añadió el mesero).",
    )
    payments: list[PaymentIn] = Field(..., min_length=1)
    discount: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    tip: Decimal = Decimal("0")


class CloseSessionIn(BaseModel):
    cash_shift_id: UUID
    billing_mode: BillingMode

    # --- unified ---
    payments: list[PaymentIn] = Field(
        default_factory=list,
        description="Solo en billing_mode='unified': pagos de la cuenta completa.",
    )
    discount: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    tip: Decimal = Decimal("0")

    # --- split ---
    splits: list[SplitPaymentIn] = Field(
        default_factory=list,
        description="Solo en billing_mode='split': un bloque de pago por comensal. "
                    "Debe cubrir a todos los que tengan consumo.",
    )


class CloseSessionResponse(BaseModel):
    table_session: TableSessionResponse
    sale_ids: list[UUID] = Field(default_factory=list)
