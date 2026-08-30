from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import (
    String, Text, Integer, Numeric, ForeignKey, DateTime, Time, CheckConstraint,
    UniqueConstraint, Index, text,
)
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List, TYPE_CHECKING
from decimal import Decimal
from datetime import datetime, time

if TYPE_CHECKING:
    from .presentation import Presentation


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
#
# `qty_price_presentation` (spec 040): precio de paquete por presentación de
# catálogo, con sus reglas en `promotion_presentation_rules`. Como `combo`, se
# calcula agrupando varias líneas y por eso NO entra en `AUTO_TYPES`
# (`service.py`) — el motor línea-por-línea no lo toca.
PROMOTION_TYPES = ("percent", "fixed", "combo", "qty_price", "qty_price_presentation")


class Promotion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Promoción del catálogo. `type` gobierna el cálculo:

    - `percent`: `value` = % de descuento (0..100) sobre el `line_total`.
    - `fixed`: `value` = monto fijo de descuento por línea aplicable.
    - `qty_price`: **`value` y `min_qty` de la promoción NO se usan.** El precio
      y el tamaño del paquete viven en cada `PromotionTarget`, porque un único
      precio dejaba la Ensalada Grande y la Pequeña al mismo par. Descuenta solo
      paquetes completos; el remanente se cobra a precio normal. Un destino sin
      precio no descuenta (ver `_pack_terms`).
    - `combo`: `value` = precio total del bundle, componentes en `combo_items`.
      Se selecciona explícitamente por `combo_id` y no participa de `evaluate`.

    Vigencia opcional: `starts_at`/`ends_at`, `days_of_week` (CSV 0=lunes..
    6=domingo) y ventana horaria `start_time`/`end_time`, que admite cruce de
    medianoche.

    **Toda la vigencia se evalúa en hora local del tenant.** Antes se evaluaba
    en UTC, lo que no solo corría la ventana horaria: en UTC-5 también corría el
    día de la semana, el día del mes y el corte de `ends_at`.
    """

    __tablename__ = "promotions"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # `String(50)` (antes 20): `qty_price_presentation` (spec 040) tiene 22
    # caracteres y no cabía en `varchar(20)` — la ampliación va en la migración
    # `f03274730367`.
    type: Mapped[str] = mapped_column(String(50), nullable=False)

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

    start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)

    min_qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    targets: Mapped[List["PromotionTarget"]] = relationship(
        back_populates="promotion", cascade="all, delete-orphan"
    )
    combo_items: Mapped[List["PromotionComboItem"]] = relationship(
        back_populates="promotion", cascade="all, delete-orphan"
    )
    presentation_rules: Mapped[List["PromotionPresentationRule"]] = relationship(
        back_populates="promotion", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('percent', 'fixed', 'combo', 'qty_price', 'qty_price_presentation')",
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
    filas de target aplica a toda la venta.

    `value` y `min_qty` son el **precio y el tamaño de paquete de este target**,
    y solo se usan en promociones `qty_price`. En NULL, el target hereda los de
    la promoción. Existen porque un único precio para todo el alcance obligaba a
    dejar la Ensalada Grande ($16.000) y la Pequeña ($9.000) al mismo precio de
    paquete, o a partir la promoción en una por producto.

    **El target más específico gana**: si una línea casa con un target de
    producto y con el de su categoría, manda el de producto. Eso es lo que
    permite "toda la categoría a $10.000, salvo la Grande a $12.000".
    """

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

    # Solo para `qty_price`. NULL = hereda el de la promoción.
    value: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    min_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(product_id IS NOT NULL) OR (category_id IS NOT NULL)",
            name="ck_promotion_target_scope",
        ),
        CheckConstraint("value IS NULL OR value >= 0", name="ck_target_value_positive"),
        CheckConstraint("min_qty IS NULL OR min_qty >= 2", name="ck_target_pack_size"),
        # Un target repetido daba igual mientras no llevara precio; con precio,
        # dos filas del mismo producto harían que el descuento dependiera del
        # orden del SELECT.
        Index(
            "uq_promotion_targets_product", "promotion_id", "product_id",
            unique=True, postgresql_where=text("product_id IS NOT NULL"),
        ),
        Index(
            "uq_promotion_targets_category", "promotion_id", "category_id",
            unique=True, postgresql_where=text("category_id IS NOT NULL"),
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


class PromotionPresentationRule(UUIDPrimaryKeyMixin, Base):
    """Regla de una promoción `qty_price_presentation` (spec 040), tabla hija de
    `promotions` — misma forma que `PromotionComboItem`.

    Una fila = la tripleta `(presentación, cantidad mínima, precio total del
    paquete)` (FR-001). `Promotion.value` NO se usa: el precio vive aquí porque
    una promoción de esta modalidad tiene un precio de paquete distinto por regla
    (research.md D3).
    """

    __tablename__ = "promotion_presentation_rules"

    promotion_id: Mapped[UUID] = mapped_column(
        ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    promotion: Mapped["Promotion"] = relationship(back_populates="presentation_rules")

    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.id", ondelete="CASCADE"), nullable=False
    )
    presentation: Mapped["Presentation"] = relationship()

    # `CHECK >= 1` — a diferencia de `qty_price` (`min_qty >= 2`), aquí `1` es
    # válido: "precio especial por unidad de esa presentación" (CL-7).
    min_qty: Mapped[int] = mapped_column(Integer, nullable=False)

    pack_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    __table_args__ = (
        CheckConstraint("min_qty >= 1", name="min_qty"),
        CheckConstraint("pack_price >= 0", name="pack_price"),
        UniqueConstraint(
            "promotion_id", "presentation_id",
            name="uq__promotion_presentation_rules__promotion_id__presentation_id",
        ),
        {"schema": "tenant"},
    )
