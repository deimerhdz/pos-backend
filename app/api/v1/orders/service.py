"""Service de órdenes: convierte el carrito en orden, descuenta inventario y
gestiona el estado. Es dueño de la transacción (commit/rollback), igual que
ProductService.
"""
import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

from app.models.cart_item import CartItem
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement
from app.models.table_session import TableSession

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

        cart_items = (
            db.execute(cart_stmt.options(selectinload(CartItem.product))).scalars().all()
        )
        if not cart_items:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El carrito está vacío",
            )

        try:
            order = Order(
                table_id=table_id,
                table_session_id=session.id if scope == "individual" else None,
                scope=scope,
                customer_name=session.customer_name if scope == "individual" else None,
                status="pending",
                total=Decimal("0.00"),
            )
            db.add(order)
            db.flush()  # asigna order.id

            total = Decimal("0.00")
            for ci in cart_items:
                product = ci.product or db.get(Product, ci.product_id)
                if product is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Producto del carrito no encontrado",
                    )
                unit_price = product.price
                subtotal = unit_price * ci.quantity
                total += subtotal

                db.add(
                    OrderItem(
                        order_id=order.id,
                        product_id=ci.product_id,
                        table_session_id=ci.table_session_id,
                        product_name=product.name,
                        quantity=ci.quantity,
                        unit_price=unit_price,
                        subtotal=subtotal,
                    )
                )

                self._deduct_stock(db, ci.product_id, ci.quantity, order.id)

            order.total = total

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

    def _deduct_stock(
        self, db: Session, product_id: UUID, quantity: int, order_id: UUID
    ) -> None:
        """Descuenta stock (movimiento 'expense') si el producto gestiona inventario.

        Productos sin fila de inventario (p. ej. RECIPE o PRODUCT sin control_stock) no
        descuentan. Lanza 400 si el stock es insuficiente.
        """
        inventory = db.execute(
            select(Inventory).where(Inventory.product_id == product_id)
        ).scalar_one_or_none()
        if inventory is None:
            return

        if quantity > inventory.stock:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Stock insuficiente para el producto {product_id}",
            )

        stock_before = inventory.stock
        stock_after = stock_before - quantity
        inventory.stock = stock_after

        db.add(
            InventoryMovement(
                quantity=quantity,
                stock_before=stock_before,
                stock_after=stock_after,
                type_movement="expense",
                reference_id=order_id,
                reason="Orden",
                product_id=product_id,
            )
        )

    # --- lectura / gestión (staff) ---

    def list_query(self, table_id: UUID | None = None, status_filter: str | None = None) -> Select:
        stmt = (
            select(Order)
            .options(selectinload(Order.items))
            .order_by(Order.created_at.desc())
        )
        if table_id is not None:
            stmt = stmt.where(Order.table_id == table_id)
        if status_filter is not None:
            stmt = stmt.where(Order.status == status_filter)
        return stmt

    def get_or_404(self, db: Session, id: UUID) -> Order:
        order = db.execute(
            select(Order).options(selectinload(Order.items)).where(Order.id == id)
        ).scalar_one_or_none()
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
            )
        return order

    def update_status(self, db: Session, id: UUID, new_status: str) -> Order:
        order = self.get_or_404(db, id)
        order.status = new_status
        db.commit()
        return self.get_or_404(db, id)
