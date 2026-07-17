"""Service de compras: registra la compra y da alta de stock por cada item
(equivalente al trigger `fn_add_inventory_on_purchase` de schema.sql, en la capa
de aplicación)."""
import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.crud import get_or_404
from app.models.inventory_item import InventoryItem
from app.models.supplier import Supplier
from app.models.purchase import Purchase, PurchaseItem
from app.api.v1.inventory.stock import record_movement
from app.api.v1.inventory.schemas import PurchaseCreate

logger = logging.getLogger(__name__)


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
                unit_cost=it.unit_cost,
            ))
            # Alta de stock + kardex + actualización del costo unitario.
            record_movement(
                db, it.inventory_item_id, type="in", quantity=it.quantity,
                reason="Compra", reference_type="purchase", reference_id=purchase.id,
                user_id=user_id,
            )
            item.unit_cost = it.unit_cost
            total += it.quantity * it.unit_cost

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
