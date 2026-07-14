from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.crud import get_or_404, ensure_unique
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.inventory_item import InventoryItem
from app.models.inventory_movement import InventoryMovement
from app.models.unit_measure import UnitMeasure
from app.models.supplier import Supplier
from app.models.purchase import Purchase
from app.api.v1.inventory import service
from app.api.v1.inventory.stock import apply_adjustment
from app.api.v1.inventory.schemas import (
    InventoryItemCreate, InventoryItemUpdate, InventoryItemResponse,
    AdjustmentIn, MovementResponse,
    SupplierCreate, SupplierUpdate, SupplierResponse,
    PurchaseCreate, PurchaseResponse,
    LowStockResponse,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])


# ============================ Insumos ============================
@router.get("/items", response_model=list[InventoryItemResponse], summary="Listar insumos")
def list_items(
    active: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(InventoryItem).order_by(InventoryItem.name)
    if active is not None:
        q = q.where(InventoryItem.active == active)
    return db.execute(q).scalars().all()


@router.get("/items/low-stock", response_model=list[LowStockResponse], summary="Insumos en o bajo el mínimo")
def low_stock(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.execute(
        select(InventoryItem).where(
            InventoryItem.active.is_(True),
            InventoryItem.current_stock <= InventoryItem.min_stock,
        ).order_by(InventoryItem.name)
    ).scalars().all()


@router.get("/items/{item_id}", response_model=InventoryItemResponse, summary="Obtener un insumo")
def get_item(item_id: UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return get_or_404(db, InventoryItem, item_id, "Inventory item not found")


@router.post("/items", response_model=InventoryItemResponse, status_code=status.HTTP_201_CREATED, summary="Crear un insumo")
def create_item(
    body: InventoryItemCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, UnitMeasure, body.unit_measure_id, "Unit measure not found")
    ensure_unique(db, InventoryItem, InventoryItem.name, body.name, "Inventory item name already exists")
    item = InventoryItem(
        name=body.name,
        unit_measure_id=body.unit_measure_id,
        type=body.type.value,
        current_stock=body.current_stock,
        min_stock=body.min_stock,
        unit_cost=body.unit_cost,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/items/{item_id}", response_model=InventoryItemResponse, summary="Actualizar un insumo")
def update_item(
    item_id: UUID,
    body: InventoryItemUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    item = get_or_404(db, InventoryItem, item_id, "Inventory item not found")
    if body.name is not None and body.name != item.name:
        ensure_unique(db, InventoryItem, InventoryItem.name, body.name, "Inventory item name already exists")
        item.name = body.name
    if body.unit_measure_id is not None:
        get_or_404(db, UnitMeasure, body.unit_measure_id, "Unit measure not found")
        item.unit_measure_id = body.unit_measure_id
    if body.type is not None:
        item.type = body.type.value
    if body.min_stock is not None:
        item.min_stock = body.min_stock
    if body.unit_cost is not None:
        item.unit_cost = body.unit_cost
    if body.active is not None:
        item.active = body.active
    db.commit()
    db.refresh(item)
    return item


@router.post("/items/{item_id}/adjust", response_model=MovementResponse, summary="Ajustar stock (delta con signo)")
def adjust_item(
    item_id: UUID,
    body: AdjustmentIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    get_or_404(db, InventoryItem, item_id, "Inventory item not found")
    movement = apply_adjustment(db, item_id, signed_delta=body.signed_delta, reason=body.reason, user_id=user.id)
    db.commit()
    db.refresh(movement)
    return movement


@router.get("/items/{item_id}/movements", response_model=list[MovementResponse], summary="Kardex de un insumo")
def item_movements(
    item_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    get_or_404(db, InventoryItem, item_id, "Inventory item not found")
    return db.execute(
        select(InventoryMovement)
        .where(InventoryMovement.inventory_item_id == item_id)
        .order_by(InventoryMovement.moved_at.desc())
    ).scalars().all()


# ============================ Proveedores ============================
@router.get("/suppliers", response_model=list[SupplierResponse], summary="Listar proveedores")
def list_suppliers(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.execute(select(Supplier).order_by(Supplier.name)).scalars().all()


@router.post("/suppliers", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED, summary="Crear proveedor")
def create_supplier(body: SupplierCreate, db: Session = Depends(get_db), _: User = Depends(require_tenant_admin)):
    supplier = Supplier(**body.model_dump())
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


@router.patch("/suppliers/{supplier_id}", response_model=SupplierResponse, summary="Actualizar proveedor")
def update_supplier(
    supplier_id: UUID,
    body: SupplierUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    supplier = get_or_404(db, Supplier, supplier_id, "Supplier not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(supplier, k, v)
    db.commit()
    db.refresh(supplier)
    return supplier


# ============================ Compras ============================
@router.get("/purchases", response_model=list[PurchaseResponse], summary="Listar compras")
def list_purchases(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.execute(select(Purchase).order_by(Purchase.purchased_at.desc())).scalars().all()


@router.post("/purchases", response_model=PurchaseResponse, status_code=status.HTTP_201_CREATED, summary="Registrar compra (da alta de stock)")
def create_purchase(
    body: PurchaseCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    return service.create_purchase(db, body, user_id=user.id)
