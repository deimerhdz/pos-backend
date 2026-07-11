"""Service de órdenes: convierte el carrito (variante + modificadores) en orden,
calcula impuestos y consume insumos por receta (motor FEFO, Fase 2). Es dueño de
la transacción (commit/rollback).
"""
import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

from app.models.cart_item import CartItem
from app.models.cart_item_modifier import CartItemModifier
from app.models.order import Order, OrderItem
from app.models.order_item_modifier import OrderItemModifier
from app.models.product import Product
from app.models.table_session import TableSession
from app.api.v1.orders.reservations import reserve_for_sale, pay_order, release_reservations
from app.api.v1.orders.taxes import compute_line_tax

logger = logging.getLogger(__name__)


class OrderService:
    # --- creación desde el carrito ---

    def create_from_cart(
        self, db: Session, *, table_id: UUID, scope: str, session: TableSession
    ) -> Order:
        if scope == "individual":
            cart_stmt = select(CartItem).where(CartItem.table_session_id == session.id)
        else:  # table
            cart_stmt = select(CartItem).where(CartItem.table_id == table_id)

        cart_items = db.execute(
            cart_stmt.options(
                selectinload(CartItem.variant),
                selectinload(CartItem.modifiers).selectinload(CartItemModifier.modifier),
            )
        ).scalars().all()
        if not cart_items:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "El carrito está vacío")

        try:
            order = Order(
                table_id=table_id,
                table_session_id=session.id if scope == "individual" else None,
                scope=scope,
                customer_name=session.customer_name if scope == "individual" else None,
                status="pending",
                subtotal=Decimal("0.00"),
                tax_total=Decimal("0.00"),
                total=Decimal("0.00"),
            )
            db.add(order)
            db.flush()

            subtotal_sum = Decimal("0.00")
            tax_sum = Decimal("0.00")
            added_sum = Decimal("0.00")
            reservation_lines: list[dict] = []

            for ci in cart_items:
                variant = ci.variant
                if variant is None:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "Item de carrito sin variante (carrito inválido).",
                    )
                product = db.get(Product, variant.product_id)
                modifiers = [m.modifier for m in ci.modifiers if m.modifier is not None]

                unit_price = variant.price + sum((m.price for m in modifiers), Decimal("0.00"))
                line_subtotal = unit_price * ci.quantity
                subtotal_sum += line_subtotal

                tax_amount, added = compute_line_tax(
                    db, product_id=variant.product_id, variant_id=variant.id, base=line_subtotal
                )
                tax_sum += tax_amount
                added_sum += added

                order_item = OrderItem(
                    order_id=order.id,
                    variant_id=variant.id,
                    product_id=variant.product_id,
                    table_session_id=ci.table_session_id,
                    product_name=product.name if product else variant.sku,
                    quantity=ci.quantity,
                    unit_price=unit_price,
                    subtotal=line_subtotal,
                    tax_amount=tax_amount,
                )
                db.add(order_item)
                db.flush()

                for m in modifiers:
                    db.add(OrderItemModifier(
                        order_item_id=order_item.id, modifier_id=m.id, name=m.name, price=m.price,
                    ))

                reservation_lines.append(
                    {"order_item_id": order_item.id, "variant_id": variant.id, "quantity": ci.quantity}
                )
                for m in modifiers:
                    reservation_lines.append(
                        {"order_item_id": order_item.id, "modifier_id": m.id, "quantity": ci.quantity}
                    )

            order.subtotal = subtotal_sum
            order.tax_total = tax_sum
            order.total = subtotal_sum + added_sum

            # Fase 2: el pedido RESERVA insumos (no consume). Bloquea 400 si no hay disponibilidad.
            reserve_for_sale(db, order, reservation_lines)

            for ci in cart_items:
                db.delete(ci)

            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.exception("Error creando la orden desde el carrito")
            raise

        return self.get_or_404(db, order.id)

    # --- lectura / gestión (staff) ---

    def list_query(self, table_id: UUID | None = None, status_filter: str | None = None) -> Select:
        stmt = (
            select(Order)
            .options(selectinload(Order.items).selectinload(OrderItem.modifiers))
            .order_by(Order.created_at.desc())
        )
        if table_id is not None:
            stmt = stmt.where(Order.table_id == table_id)
        if status_filter is not None:
            stmt = stmt.where(Order.status == status_filter)
        return stmt

    def get_or_404(self, db: Session, id: UUID) -> Order:
        order = db.execute(
            select(Order)
            .options(selectinload(Order.items).selectinload(OrderItem.modifiers))
            .where(Order.id == id)
        ).scalar_one_or_none()
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
        return order

    def update_status(self, db: Session, id: UUID, new_status: str) -> Order:
        order = self.get_or_404(db, id)
        # Cancelar libera las reservas activas (sin mover inventario).
        if new_status == "cancelled":
            release_reservations(db, order)
        order.status = new_status
        db.commit()
        return self.get_or_404(db, id)

    def pay(self, db: Session, id: UUID) -> Order:
        """Cobro en caja: consume (FEFO) las reservas de la orden y la completa."""
        order = self.get_or_404(db, id)
        if order.status != "pending":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"La orden no está pendiente de cobro (estado: {order.status}).",
            )
        try:
            pay_order(db, order)
            order.status = "completed"
            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.exception("Error cobrando la orden")
            raise
        return self.get_or_404(db, id)
