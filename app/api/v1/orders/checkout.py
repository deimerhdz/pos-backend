"""Cobro y ciclo de cierre de una orden de mesa (Fase 7): bloqueo con lock
optimista + validación de cocina, cuenta/split por comensal, pago (crea Sale sin
re-descontar), cancelación con reversa, y liberación de mesa."""
import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.crud import get_or_404
from app.core.models import User
from app.models.dining_table import DiningTable
from app.models.dining_session import DiningSession
from app.models.cart import Cart
from app.models.option import Option
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.cash_shift import CashShift
from app.models.payment import Payment, PaymentMethod
from app.models.sale import Sale, SaleItem
from app.models.customer_order import CustomerOrder
from app.models.order_item import OrderItem
from app.models.order_cancel_log import OrderCancelLog
from app.api.v1.orders.consumption import reverse_order_item
from app.api.v1.orders.schemas import (
    BlockIn, CancelIn, PayIn,
    BillResponse, BillOrderLine, BillItemLine, BillSessionLine,
)

logger = logging.getLogger(__name__)

# Estados de cocina que impiden bloquear para cobro.
_NOT_READY = ("pendiente", "en_preparacion")
TERMINAL = ("pagada", "cancelada")


def _item_options(db: Session, item: OrderItem) -> list[Option]:
    opt_ids = [o.option_id for o in item.options]
    if not opt_ids:
        return []
    return db.execute(select(Option).where(Option.id.in_(opt_ids))).scalars().all()


def _reload_order(db: Session, order_id: UUID) -> CustomerOrder:
    return db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
    ).scalar_one()


# --------------------------------------------------------------------- Bloqueo

def block_order(db: Session, order_id: UUID, data: BlockIn) -> CustomerOrder:
    try:
        order = db.execute(
            select(CustomerOrder).where(CustomerOrder.id == order_id).with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
        if order.status != "abierta":
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"La orden no está abierta (status={order.status})"
            )
        if order.version != data.version:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"error": "Conflicto de versión (la orden cambió)", "version_actual": order.version},
            )

        pendientes = db.execute(
            select(OrderItem).where(
                OrderItem.order_id == order.id,
                OrderItem.estado_cocina.in_(_NOT_READY),
            )
        ).scalars().all()
        if pendientes:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "error": "Hay ítems sin terminar en cocina; anúlalos o espera a que estén listos.",
                    "items": [
                        {
                            "order_item_id": str(it.id),
                            "product_variant_id": str(it.product_variant_id),
                            "estado_cocina": it.estado_cocina,
                            "session_id": str(it.session_id) if it.session_id else None,
                        }
                        for it in pendientes
                    ],
                },
            )

        order.status = "bloqueada"
        order.version += 1
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error bloqueando orden para cobro")
        raise

    return _reload_order(db, order_id)


# ----------------------------------------------------------------- Cuenta/split

def compute_bill(db: Session, table_id: UUID) -> BillResponse:
    table = get_or_404(db, DiningTable, table_id, "Table not found")

    orders = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items))
        .where(
            CustomerOrder.dining_table_id == table.id,
            CustomerOrder.status != "cancelada",
        )
        .order_by(CustomerOrder.created_at)
    ).scalars().all()

    # nombres de comensal por sesión
    names = dict(db.execute(select(DiningSession.id, DiningSession.customer_name)).all())

    total = Decimal("0")
    order_lines: list[BillOrderLine] = []
    split: dict[UUID | None, Decimal] = {}

    for order in orders:
        subtotal = Decimal("0")
        items: list[BillItemLine] = []
        for it in order.items:
            if it.estado_cocina == "anulado":
                continue
            line_total = Decimal(it.unit_price) * it.quantity
            subtotal += line_total
            split[it.session_id] = split.get(it.session_id, Decimal("0")) + line_total
            items.append(BillItemLine(
                order_item_id=it.id, product_variant_id=it.product_variant_id,
                session_id=it.session_id, quantity=it.quantity,
                unit_price=it.unit_price, line_total=line_total,
                estado_cocina=it.estado_cocina,
            ))
        total += subtotal
        order_lines.append(BillOrderLine(
            order_id=order.id, status=order.status, subtotal=subtotal, items=items,
        ))

    split_lines = [
        BillSessionLine(
            session_id=sid,
            customer_name=names.get(sid) if sid else None,
            subtotal=amount,
        )
        for sid, amount in split.items()
    ]

    return BillResponse(
        dining_table_id=table.id, total=total, orders=order_lines, split=split_lines,
    )


# ------------------------------------------------------------------------ Pago

def pay_order(db: Session, order_id: UUID, data: PayIn, cashier: User) -> Sale:
    order = get_or_404(db, CustomerOrder, order_id, "Order not found")
    if order.status != "bloqueada":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "La orden debe estar bloqueada para cobrar (bloquea primero).",
        )
    shift = get_or_404(db, CashShift, data.cash_shift_id, "Shift not found")
    if shift.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "El turno de caja está cerrado")

    try:
        sale = Sale(
            cash_shift_id=shift.id,
            dining_table_id=order.dining_table_id,
            dining_session_id=order.dining_session_id,
            customer_order_id=order.id,
            user_id=cashier.id,
            user_name=cashier.name,
            customer_name=order.customer_name,
            discount=data.discount, tax=data.tax, tip=data.tip,
            status="issued",
        )
        db.add(sale)
        db.flush()

        items = db.execute(
            select(OrderItem)
            .options(selectinload(OrderItem.options))
            .where(OrderItem.order_id == order.id, OrderItem.estado_cocina != "anulado")
        ).scalars().all()
        if not items:
            raise HTTPException(status.HTTP_409_CONFLICT, "La orden no tiene ítems cobrables")

        subtotal = Decimal("0")
        for it in items:
            variant = db.get(ProductVariant, it.product_variant_id)
            product = db.get(Product, variant.product_id) if variant else None
            description = f"{product.name} - {variant.name}" if product else (variant.name if variant else "")
            options_snapshot: list[dict] = []
            for opt in _item_options(db, it):
                options_snapshot.append({
                    "option_id": str(opt.id), "name": opt.name,
                    "extra_price": str(opt.extra_price),
                })
            line_total = Decimal(it.unit_price) * it.quantity
            subtotal += line_total
            db.add(SaleItem(
                sale_id=sale.id, product_variant_id=it.product_variant_id,
                description=description, options=options_snapshot,
                quantity=it.quantity, unit_price=it.unit_price, line_total=line_total,
            ))

        total = subtotal - Decimal(data.discount) + Decimal(data.tax) + Decimal(data.tip)
        if total < 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El total no puede ser negativo")

        paid = Decimal("0")
        for p in data.payments:
            get_or_404(db, PaymentMethod, p.payment_method_id, "Payment method not found")
            db.add(Payment(
                sale_id=sale.id, payment_method_id=p.payment_method_id,
                amount=p.amount, reference=p.reference,
            ))
            paid += Decimal(p.amount)
        if paid < total:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"El pago ({paid}) no cubre el total ({total})",
            )

        sale.subtotal = subtotal
        sale.total = total
        sale.status = "paid"
        order.status = "pagada"
        # NO se llama deduct_sale: el inventario ya se descontó en la consolidación.

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error cobrando la orden de mesa")
        raise

    return db.execute(
        select(Sale)
        .options(selectinload(Sale.items), selectinload(Sale.payments))
        .where(Sale.id == sale.id)
    ).scalar_one()


# ------------------------------------------------------------------ Cancelación

def cancel_order(db: Session, order_id: UUID, data: CancelIn, user: User) -> CustomerOrder:
    order = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order.status in TERMINAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"La orden ya es terminal (status={order.status})"
        )

    try:
        for it in order.items:
            if it.estado_cocina == "anulado":
                continue
            reverse_order_item(db, it, _item_options(db, it), user.id, reference_id=order.id)

        order.status = "cancelada"
        db.add(OrderCancelLog(
            order_id=order.id, motivo=data.motivo,
            user_id=user.id, user_name=user.name,
        ))
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error cancelando la orden")
        raise

    return _reload_order(db, order_id)


# ------------------------------------------------------------- Liberar mesa

def release_table(db: Session, table_id: UUID) -> DiningTable:
    table = get_or_404(db, DiningTable, table_id, "Table not found")

    blocking = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items))
        .where(
            CustomerOrder.dining_table_id == table.id,
            CustomerOrder.status.notin_(TERMINAL),
        )
    ).scalars().all()
    if blocking:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "La mesa tiene órdenes sin cerrar (paga o cancela primero).",
                "orders": [
                    {
                        "order_id": str(o.id),
                        "status": o.status,
                        "items": len([i for i in o.items if i.estado_cocina != "anulado"]),
                    }
                    for o in blocking
                ],
            },
        )

    try:
        table.status = "libre"
        sessions = db.execute(
            select(DiningSession).where(
                DiningSession.dining_table_id == table.id,
                DiningSession.status == "open",
            )
        ).scalars().all()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for s in sessions:
            s.status = "closed"
            if s.closed_at is None:
                s.closed_at = now
            for cart in db.execute(
                select(Cart).where(Cart.session_id == s.id, Cart.status == "abierto")
            ).scalars():
                cart.status = "abandonado"
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Error liberando la mesa")
        raise

    db.refresh(table)
    return table
