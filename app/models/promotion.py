from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import (
    String, Boolean, Integer, Numeric, ForeignKey, DateTime, Time, CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List, TYPE_CHECKING
from decimal import Decimal
from datetime import datetime, time


class Promotion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Promoción del catálogo (RF-008..012). `type` gobierna el cálculo:
    - `percent`: `value` = % de descuento (0..100).
    - `fixed`: `value` = monto fijo de descuento por línea aplicable.
    - `combo`: `value` = precio total del bundle; los componentes viven en
      `combo_items` (producto + cantidad requerida). Se selecciona
      explícitamente (`combo_id` en vez de `product_variant_id` al agregar un
      ítem); el servicio la expande en líneas reales de cada componente a su
      precio normal y el ahorro se aplica como descuento de venta.
    - `buy_x_get_y` / `qty_price`: reservados (fase 2; hoy no descuentan).

    Vigencia opcional por rango de fechas (`starts_at`/`ends_at`), días de la
    semana (`days_of_week`, CSV 0=lunes..6=domingo), días del mes
    (`days_of_month`, CSV 1..31, ej. quincena "15,30") y ventana horaria
    (`start_time`/`end_time`). El alcance de `percent`/`fixed` se define en
    `promotion_targets` (producto o categoría); sin targets = aplica a toda la
    venta. `combo` no usa `promotion_targets`, usa `combo_items`."""

    __tablename__ = "promotions"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    type: Mapped[str] = mapped_column(String(20), nullable=False)

    value: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    days_of_week: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    days_of_month: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)

    # Parámetros para tipos de fase 2.
    min_qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    buy_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    get_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    targets: Mapped[List["PromotionTarget"]] = relationship(
        back_populates="promotion", cascade="all, delete-orphan"
    )
    combo_items: Mapped[List["PromotionComboItem"]] = relationship(
        back_populates="promotion", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('percent', 'fixed', 'buy_x_get_y', 'combo', 'qty_price')",
            name="ck_promotion_type",
        ),
        CheckConstraint("value >= 0", name="ck_promotion_value_positive"),
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
    bundle; el ahorro se calcula comparando el precio normal de estos
    componentes contra ese precio total."""

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
