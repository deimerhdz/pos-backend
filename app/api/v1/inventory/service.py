"""Service de compras: registra la compra y da alta de stock por cada item
(equivalente al trigger `fn_add_inventory_on_purchase` de schema.sql, en la capa
de aplicación)."""
import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.core.crud import get_or_404
from app.models.inventory_item import InventoryItem
from app.models.supplier import Supplier
from app.models.purchase import Purchase, PurchaseItem
from app.api.v1.inventory.stock import record_movement
from app.api.v1.inventory.schemas import PurchaseCreate, PurchaseReceiveIn
from app.core import inventory_reasons as reasons

logger = logging.getLogger(__name__)


def list_items_query(
    search: str | None = None,
    type_: str | None = None,
    active: bool | None = None,
    low_stock: bool | None = None,
) -> Select:
    """Construye el Select filtrado/ordenado para GET /inventory/items.

    `paginate()` (app/core/pagination.py) aplica offset/limit sobre este Select.
    """
    stmt = select(InventoryItem).order_by(InventoryItem.name)
    if search:
        stmt = stmt.where(InventoryItem.name.ilike(f"%{search}%"))
    if type_ is not None:
        stmt = stmt.where(InventoryItem.type == type_)
    if active is not None:
        stmt = stmt.where(InventoryItem.active == active)
    if low_stock:
        stmt = stmt.where(InventoryItem.current_stock <= InventoryItem.min_stock)
    return stmt


def create_purchase(db: Session, data: PurchaseCreate, user_id: UUID | None) -> Purchase:
    if data.supplier_id is not None:
        get_or_404(db, Supplier, data.supplier_id, "Supplier not found")

    try:
        purchase = Purchase(
            supplier_id=data.supplier_id,
            invoice_number=data.invoice_number,
            user_id=user_id,
            total=Decimal("0"),
        )
        db.add(purchase)
        db.flush()

        total = Decimal("0")
        for it in data.items:
            item = get_or_404(db, InventoryItem, it.inventory_item_id, "Inventory item not found")
            db.add(PurchaseItem(
                purchase_id=purchase.id,
                inventory_item_id=it.inventory_item_id,
                quantity=it.quantity,
                received_quantity=it.quantity,  # alta directa: recibida por completo
                unit_cost=it.unit_cost,
            ))
            # Alta de stock + kardex + actualización del costo unitario.
            record_movement(
                db, it.inventory_item_id, type="in", quantity=it.quantity,
                reason=reasons.COMPRA, reference_type=reasons.REF_PURCHASE, reference_id=purchase.id,
                user_id=user_id,
            )
            item.unit_cost = it.unit_cost
            total += it.quantity * it.unit_cost

        purchase.status = "received"
        purchase.total = total
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error registrando compra")
        raise
    db.refresh(purchase)
    return purchase


def create_purchase_order(db: Session, data: PurchaseCreate, user_id: UUID | None) -> Purchase:
    """Crea una orden de compra en estado `draft` SIN dar alta de stock (RF-022).
    El stock se suma luego con `receive_purchase` (parcial o total)."""
    if data.supplier_id is not None:
        get_or_404(db, Supplier, data.supplier_id, "Supplier not found")
    try:
        purchase = Purchase(
            supplier_id=data.supplier_id, invoice_number=data.invoice_number,
            user_id=user_id, status="draft", total=Decimal("0"),
        )
        db.add(purchase)
        db.flush()
        total = Decimal("0")
        for it in data.items:
            get_or_404(db, InventoryItem, it.inventory_item_id, "Inventory item not found")
            db.add(PurchaseItem(
                purchase_id=purchase.id, inventory_item_id=it.inventory_item_id,
                quantity=it.quantity, received_quantity=Decimal("0"), unit_cost=it.unit_cost,
            ))
            total += it.quantity * it.unit_cost
        purchase.total = total
        db.commit()
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); logger.exception("Error creando orden de compra"); raise
    db.refresh(purchase)
    return purchase


def receive_purchase(db: Session, purchase_id: UUID, data: PurchaseReceiveIn,
                     user_id: UUID | None) -> Purchase:
    """Recibe (parcial o totalmente) una orden de compra: suma stock por los
    ítems indicados, incrementa `received_quantity` y ajusta el estado."""
    purchase = get_or_404(db, Purchase, purchase_id, "Purchase not found")
    if purchase.status == "received":
        raise HTTPException(409, "La compra ya fue recibida por completo")

    items = {i.id: i for i in purchase.items}
    try:
        for r in data.items:
            pi = items.get(r.purchase_item_id)
            if pi is None:
                raise HTTPException(404, f"Ítem de compra {r.purchase_item_id} no encontrado")
            remaining = Decimal(pi.quantity) - Decimal(pi.received_quantity)
            if r.quantity > remaining:
                raise HTTPException(
                    422,
                    f"Recepción {r.quantity} excede lo pendiente ({remaining}) del ítem {pi.id}",
                )
            record_movement(
                db, pi.inventory_item_id, type="in", quantity=r.quantity,
                reason=reasons.COMPRA, reference_type=reasons.REF_PURCHASE,
                reference_id=purchase.id, user_id=user_id,
            )
            pi.received_quantity = Decimal(pi.received_quantity) + r.quantity
            inv = db.get(InventoryItem, pi.inventory_item_id)
            if inv is not None:
                inv.unit_cost = pi.unit_cost

        fully = all(Decimal(i.received_quantity) >= Decimal(i.quantity) for i in purchase.items)
        any_received = any(Decimal(i.received_quantity) > 0 for i in purchase.items)
        purchase.status = "received" if fully else ("partial" if any_received else "draft")
        db.commit()
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); logger.exception("Error recibiendo compra"); raise
    db.refresh(purchase)
    return purchase
