from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import (
    String, Text, Integer, Numeric, ForeignKey, DateTime, Time, CheckConstraint,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, time


# Máquina de estados del RF. `draft` es el único estado en el que se puede
# cambiar `type`, `targets` y `combo_items`: una vez activada, la promoción ya
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

# `buy_x_get_y` sale del dominio: mientras `_line_discount` le devuelva 0, ser
# configurable solo sirve para que un admin cree un "2x1" que no descuenta.
PROMOTION_TYPES = ("percent", "fixed", "combo", "qty_price")


class Promotion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Promoción del catálogo. `type` gobierna el cálculo:

    - `percent`: `value` = % de descuento (0..100) sobre el `line_total`.
    - `fixed`: `value` = monto fijo de descuento por línea aplicable.
    - `qty_price`: `min_qty` = unidades del paquete y `value` = precio total de
      ese paquete ("compra 2 granizados y paga X"). Descuenta solo paquetes
      completos; el remanente se cobra a precio normal.
    - `combo`: `value` = precio total del bundle, componentes en `combo_items`.
      Se selecciona explícitamente por `combo_id` y no participa de `evaluate`.

    Vigencia opcional: `starts_at`/`ends_at`, `days_of_week` (CSV 0=lunes..
    6=domingo), `days_of_month` (CSV 1..31) y ventana horaria
    `start_time`/`end_time`, que admite cruce de medianoche.

    **Toda la vigencia se evalúa en hora local del tenant.** Antes se evaluaba
    en UTC, lo que no solo corría la ventana horaria: en UTC-5 también corría el
    día de la semana, el día del mes y el corte de `ends_at`.
    """

    __tablename__ = "promotions"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    type: Mapped[str] = mapped_column(String(20), nullable=False)

    value: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="draft", index=True
    )

    # Resuelve el conflicto cuando varias promociones aplican a la misma línea.
    # Mayor gana; empate se rompe por descuento mayor y luego por `created_at`.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    days_of_week: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    days_of_month: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)

    min_qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    targets: Mapped[List["PromotionTarget"]] = relationship(
        back_populates="promotion", cascade="all, delete-orphan"
    )
    combo_items: Mapped[List["PromotionComboItem"]] = relationship(
        back_populates="promotion", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('percent', 'fixed', 'combo', 'qty_price')",
            name="ck_promotion_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'finished')",
            name="ck_promotion_status",
        ),
        CheckConstraint("value >= 0", name="ck_promotion_value_positive"),
        # El rango porcentual deja de depender solo de Pydantic: `PromotionUpdate`
        # no lo validaba, y un PATCH con `value=500` sobre un percent hacía que
        # `build_sale` rechazara con "El total no puede ser negativo" cualquier
        # venta que tocara esa categoría. Un typo de configuración tumbaba la caja.
        CheckConstraint(
            "type <> 'percent' OR value <= 100",
            name="ck_promotion_percent_range",
        ),
        # Un `qty_price` de paquete 1 es un precio, no una promoción.
        CheckConstraint(
            "type <> 'qty_price' OR min_qty >= 2",
            name="ck_promotion_qty_price_pack",
        ),
        # `active_discount_promotions` filtra estado y fecha de corte en SQL:
        # este índice es lo que evita el escaneo completo de la tabla en cada
        # `GET /menu` y `GET /cart` públicos.
        Index("ix_promotions_status_ends_at", "status", "ends_at"),
        {"schema": "tenant"},
    )


class PromotionTarget(UUIDPrimaryKeyMixin, Base):
    """Alcance de una promoción: un producto o una categoría. Una promoción sin
    filas de target aplica a toda la venta."""

    __tablename__ = "promotion_targets"

    promotion_id: Mapped[UUID] = mapped_column(
        ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    promotion: Mapped["Promotion"] = relationship(back_populates="targets")

    product_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True
    )

    category_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), nullable=True, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "(product_id IS NOT NULL) OR (category_id IS NOT NULL)",
            name="ck_promotion_target_scope",
        ),
        {"schema": "tenant"},
    )


class PromotionComboItem(UUIDPrimaryKeyMixin, Base):
    """Componente de un combo (`Promotion.type == 'combo'`): variante requerida
    y cantidad por unidad de combo. `Promotion.value` es el precio total del
    bundle."""

    __tablename__ = "promotion_combo_items"

    promotion_id: Mapped[UUID] = mapped_column(
        ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    promotion: Mapped["Promotion"] = relationship(back_populates="combo_items")

    product_variant_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_promotion_combo_item_qty_positive"),
        UniqueConstraint(
            "promotion_id", "product_variant_id",
            name="uq__promotion_combo_items__promotion_id__product_variant_id",
        ),
        {"schema": "tenant"},
    )
