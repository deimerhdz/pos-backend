from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import (
    String, Text, Integer, Numeric, ForeignKey, DateTime, Time, CheckConstraint,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List, TYPE_CHECKING
from decimal import Decimal
from datetime import datetime, time

if TYPE_CHECKING:
    from .product_variant import ProductVariant


# Máquina de estados del RF. `draft` es el único estado en el que se puede
# cambiar `type` y el conjunto de variantes: una vez activada, la promoción ya
# pudo explicar el descuento de una venta y reescribir su forma reescribiría la
# historia. Para cambiar la forma se duplica (`POST /promotions/{id}/duplicate`).
PROMOTION_STATUSES = ("draft", "active", "paused", "finished")

# Transiciones permitidas. `finished` es terminal: una promoción vencida no
# revive, se duplica.
PROMOTION_TRANSITIONS = {
    "draft": {"active", "finished"},
    "active": {"paused", "finished"},
    "paused": {"active", "finished"},
    "finished": set(),
}

# spec 063: los tipos **vivos** son solo `percent` y `package_price`. Los valores
# viejos (`fixed`/`combo`/`qty_price`/`qty_price_presentation`) SE CONSERVAN aquí:
# la migración `063a` dejó las promociones no terminales de esos tipos en
# `finished` con su `type` histórico, y `PromotionResponse.type` (str libre) debe
# poder serializarlas (FR-025, A-62). La restricción "solo dos tipos vivos" vive
# en el enum de ENTRADA de Pydantic (`PromotionType` en `schemas.py`) y en el
# `ck_promotion_type` con escape `OR status='finished'` (`063b`).
PROMOTION_TYPES = (
    "percent", "fixed", "combo", "qty_price", "qty_price_presentation", "package_price",
)


class Promotion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Promoción del catálogo (spec 063). `type` gobierna el cálculo:

    - `percent`: `value` = % de descuento (0 < value <= 100).
    - `package_price`: `value` = precio total de `min_qty` unidades cualesquiera
      del conjunto de variantes (`promotion_variants`).

    Vigencia opcional: `starts_at`/`ends_at`, `days_of_week` (CSV 0=lunes..
    6=domingo) y ventana horaria `start_time`/`end_time`, que admite cruce de
    medianoche. **Toda la vigencia se evalúa en hora local del tenant** (A-08).
    """

    __tablename__ = "promotions"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # `String(50)`: cabe `qty_price_presentation` (22) de las promociones
    # `finished` históricas y `package_price` (13).
    type: Mapped[str] = mapped_column(String(50), nullable=False)

    value: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="draft", index=True
    )

    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    days_of_week: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)

    min_qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # spec 063 (FR-025): `NULL` salvo en las promociones que la migración `063a`
    # pasó a `finished`. Fuente del aviso "recrea a mano"
    # (`GET /promotions?closed_by_refactor=true`).
    closed_by_refactor_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    # spec 063 (FR-001): conjunto explícito de variantes elegibles.
    variants: Mapped[List["PromotionVariant"]] = relationship(
        back_populates="promotion", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # spec 063 (`063b`): estrechado CON ESCAPE — ninguna promoción viva puede
        # tener un tipo viejo, pero las `finished` históricas conservan el suyo.
        CheckConstraint(
            "type IN ('percent', 'package_price') OR status = 'finished'",
            name="ck_promotion_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'finished')",
            name="ck_promotion_status",
        ),
        CheckConstraint("value >= 0", name="ck_promotion_value_positive"),
        CheckConstraint("min_qty >= 1", name="ck_promotion_min_qty"),
        CheckConstraint(
            "type <> 'percent' OR value <= 100",
            name="ck_promotion_percent_range",
        ),
        # `active_variant_set_promotions` filtra estado y fecha de corte en SQL.
        Index("ix_promotions_status_ends_at", "status", "ends_at"),
        {"schema": "tenant"},
    )


class PromotionVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """spec 063 (FR-001) — una fila = "esta variante pertenece al conjunto
    elegible de esta promoción". Tabla hija de `promotions`.

    **No lleva `type`, `value` ni `min_qty`**: la única combinación (tipo, valor,
    cantidad mínima) vive en la `Promotion`. Es lo que permite que un paquete
    combine variantes distintas del conjunto (clarification 2026-08-31).

    `ondelete="CASCADE"` en `product_variant_id`: FR-011 — una variante
    **eliminada** sale del conjunto de toda promoción sin dejar fila huérfana.
    Una variante **desactivada** no se borra: el motor la filtra por
    `product_variants.active`.
    """

    __tablename__ = "promotion_variants"

    promotion_id: Mapped[UUID] = mapped_column(
        ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    promotion: Mapped["Promotion"] = relationship(back_populates="variants")

    product_variant_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_variant: Mapped["ProductVariant"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "promotion_id", "product_variant_id",
            name="uq__promotion_variants__promotion_id__product_variant_id",
        ),
        {"schema": "tenant"},
    )
