from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.crud import get_or_404, ensure_unique
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.core.pagination import Page, paginate
from app.core.plan_limits import require_module_access
from app.models.inventory_item import InventoryItem
from app.models.inventory_movement import InventoryMovement
from app.models.unit_measure import UnitMeasure
from app.models.supplier import Supplier
from app.models.purchase import Purchase
from app.api.v1.inventory import service
from app.api.v1.inventory.stock import apply_adjustment
from app.api.v1.inventory.schemas import (
    InventoryItemCreate, InventoryItemUpdate, InventoryItemResponse, InventoryItemType,
    AdjustmentIn, MovementResponse,
    SupplierCreate, SupplierUpdate, SupplierResponse,
    PurchaseCreate, PurchaseResponse, PurchaseReceiveIn,
    LowStockResponse,
)

router = APIRouter(
    prefix="/inventory", tags=["inventory"],
    dependencies=[Depends(require_module_access("inventario"))],
)


# ============================ Insumos ============================
@router.get("/items", response_model=Page[InventoryItemResponse], summary="Listar insumos")
def list_items(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Búsqueda por nombre (contiene, sin distinguir mayúsculas)"),
    type: InventoryItemType | None = Query(None, description="Filtrar por tipo de insumo"),
    active: bool | None = Query(None),
    low_stock: bool | None = Query(None, description="Solo insumos en o bajo el mínimo"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = service.list_items_query(
        search=search.strip() if search else None,
        type_=type.value if type is not None else None,
        active=active,
        low_stock=low_stock,
    )
    return paginate(db, stmt, page, size)


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


@router.get("/items/{item_id}/movements", response_model=Page[MovementResponse], summary="Kardex de un insumo")
def item_movements(
    item_id: UUID,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    get_or_404(db, InventoryItem, item_id, "Inventory item not found")
    stmt = (
        select(InventoryMovement)
        .where(InventoryMovement.inventory_item_id == item_id)
        .order_by(InventoryMovement.moved_at.desc())
    )
    return paginate(db, stmt, page, size)


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
@router.get(
    "/purchases", response_model=Page[PurchaseResponse], summary="Listar compras",
    dependencies=[Depends(require_module_access("compras"))],
)
def list_purchases(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Purchase).order_by(Purchase.purchased_at.desc())
    return paginate(db, stmt, page, size)


@router.post(
    "/purchases", response_model=PurchaseResponse, status_code=status.HTTP_201_CREATED,
    summary="Registrar compra (da alta de stock total)",
    dependencies=[Depends(require_module_access("compras"))],
)
def create_purchase(
    body: PurchaseCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    return service.create_purchase(db, body, user_id=user.id)


@router.post(
    "/purchases/order", response_model=PurchaseResponse, status_code=status.HTTP_201_CREATED,
    summary="Crear orden de compra (draft, sin alta de stock) — RF-022",
    dependencies=[Depends(require_module_access("compras"))],
)
def create_purchase_order(
    body: PurchaseCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    return service.create_purchase_order(db, body, user_id=user.id)


@router.post(
    "/purchases/{purchase_id}/receive", response_model=PurchaseResponse,
    summary="Recibir una orden de compra (parcial o total) — RF-022",
    dependencies=[Depends(require_module_access("compras"))],
)
def receive_purchase(
    purchase_id: UUID,
    body: PurchaseReceiveIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    return service.receive_purchase(db, purchase_id, body, user_id=user.id)
