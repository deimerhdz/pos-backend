from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Integer, Numeric, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List, TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .customer_order import CustomerOrder

#: Ítems que aún no están terminados. Es la misma regla en tres sitios —bloquear
#: el cobro, cerrar la sesión de mesa y marcar listo un pedido entero—, así que
#: vive junto al modelo dueño del campo en vez de copiarse en cada uno.
EN_CURSO = ("pendiente", "en_preparacion")


class OrderItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "order_items"

    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("customer_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order: Mapped["CustomerOrder"] = relationship(back_populates="items")

    # Comensal al que se le carga esta línea. La asignación es **por ítem**, no
    # por pedido: así el split de cuenta es exacto aunque un pedido mezcle
    # comensales. Nullable: mostrador, o línea agregada por el mesero.
    participant_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("session_participants.id"), nullable=True, index=True
    )

    product_variant_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_variants.id"), nullable=False, index=True
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )

    # Snapshot del descuento vigente al momento de confirmar el pedido (spec
    # 038, FR-013): `None` si ninguna promoción aplicó a esta línea (o es una
    # línea de combo, cuyo ahorro se calcula aparte). Nullable sin default:
    # las filas anteriores a esta spec quedan en NULL, sin backfill (FR-015).
    discounted_unit_price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    discounted_line_total: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    # Promoción de combo que generó esta línea (selección explícita, copiada
    # tal cual desde el cart_item). Varias líneas comparten el mismo combo_id:
    # son los componentes de un mismo combo.
    combo_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("promotions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Ciclo de preparación del ítem, independiente del status de pago de la orden.
    # Las transiciones pendiente→en_preparacion→listo las mueve la terminal de
    # mesas. 'anulado' se excluye de la validación de bloqueo de cobro.
    estado_cocina: Mapped[str] = mapped_column(
        String(15), nullable=False, server_default="pendiente"
    )

    # Si este ítem reemplaza a uno anulado (void + recreación, Fase 6).
    void_de: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("order_items.id"), nullable=True
    )

    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    options: Mapped[List["OrderItemOption"]] = relationship(
        back_populates="order_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_item_quantity_positive"),
        CheckConstraint(
            "estado_cocina IN ('pendiente', 'en_preparacion', 'listo', 'anulado')",
            name="ck_order_item_estado_cocina",
        ),
        {"schema": "tenant"},
    )


class OrderItemOption(UUIDPrimaryKeyMixin, Base):
    """Opción elegida en una línea de comanda (p.ej. un sabor)."""

    __tablename__ = "order_item_options"

    order_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_item: Mapped["OrderItem"] = relationship(back_populates="options")

    option_id: Mapped[UUID] = mapped_column(
        ForeignKey("options.id"), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "order_item_id", "option_id",
            name="uq__order_item_options__order_item_id__option_id",
        ),
        {"schema": "tenant"},
    )
