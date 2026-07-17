from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.exc import IntegrityError

from app.core.db import get_db, get_tenant
from app.core.crud import get_or_404
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User, Tenant
from app.core.qr_token import mint_qr_token
from app.models.dining_table import DiningTable
from app.models.dining_session import DiningSession
from app.models.customer_order import CustomerOrder
from app.models.order_item import OrderItem
from app.api.v1.orders import service
from app.api.v1.orders.schemas import (
    TableCreate, TableUpdate, TableResponse, TableQrTokenResponse,
    SessionOpen, SessionResponse,
    OrderCreate, OrderStatusUpdate, OrderResponse,
)

router = APIRouter(prefix="/orders", tags=["orders"])


def _load_order(db: Session, order_id: UUID) -> CustomerOrder:
    order = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return order


# ============================ Mesas (staff) ============================
@router.get("/tables", response_model=list[TableResponse], summary="Listar mesas")
def list_tables(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.execute(select(DiningTable).order_by(DiningTable.number)).scalars().all()


@router.post("/tables", response_model=TableResponse, status_code=status.HTTP_201_CREATED, summary="Crear mesa (genera qr_token)")
def create_table(body: TableCreate, db: Session = Depends(get_db), _: User = Depends(require_tenant_admin)):
    dup = db.execute(select(DiningTable).where(DiningTable.number == body.number)).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe una mesa con ese número")
    table = DiningTable(number=body.number, name=body.name)
    db.add(table)
    db.commit()
    db.refresh(table)
    return table


@router.patch("/tables/{table_id}", response_model=TableResponse, summary="Actualizar mesa")
def update_table(
    table_id: UUID, body: TableUpdate,
    db: Session = Depends(get_db), _: User = Depends(require_tenant_admin),
):
    table = get_or_404(db, DiningTable, table_id, "Table not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(table, k, v)
    db.commit()
    db.refresh(table)
    return table


@router.get(
    "/tables/{table_id}/qr-token",
    response_model=TableQrTokenResponse,
    summary="Emitir token firmado del QR de la mesa (imprimible)",
)
def issue_table_qr_token(
    table_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
    _: User = Depends(require_tenant_admin),
):
    table = get_or_404(db, DiningTable, table_id, "Table not found")
    token = mint_qr_token(tenant.id, table.id)
    return TableQrTokenResponse(
        table_id=table.id,
        number=table.number,
        qr_token=token,
        menu_path=f"/api/v1/menu/qr-token/{token}",
    )


# ============================ Sesiones (público, QR) ============================
@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED, summary="Abrir sesión de mesa por QR")
def open_session(body: SessionOpen, db: Session = Depends(get_db)):
    table = db.execute(
        select(DiningTable).where(DiningTable.qr_token == body.qr_token, DiningTable.active.is_(True))
    ).scalar_one_or_none()
    if table is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mesa no encontrada o inactiva")
    session = DiningSession(dining_table_id=table.id, customer_name=body.customer_name, status="open")
    db.add(session)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "La mesa ya tiene una sesión abierta")
    db.refresh(session)
    return session


@router.post("/sessions/{session_id}/close", response_model=SessionResponse, summary="Cerrar sesión de mesa")
def close_session(session_id: UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    from datetime import datetime
    session = get_or_404(db, DiningSession, session_id, "Session not found")
    session.status = "closed"
    session.closed_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    return session


# ============================ Comandas ============================
@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED, summary="Crear comanda (QR/mostrador)")
def create_order(body: OrderCreate, db: Session = Depends(get_db)):
    order = service.create_order(db, body, user_id=None)
    return _load_order(db, order.id)


@router.get("", response_model=list[OrderResponse], summary="Listar comandas (staff)")
def list_orders(
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(CustomerOrder).options(
        selectinload(CustomerOrder.items).selectinload(OrderItem.options)
    ).order_by(CustomerOrder.created_at.desc())
    if status_filter is not None:
        q = q.where(CustomerOrder.status == status_filter)
    return db.execute(q).scalars().all()


@router.get("/{order_id}", response_model=OrderResponse, summary="Obtener una comanda")
def get_order(order_id: UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _load_order(db, order_id)


@router.patch("/{order_id}/status", response_model=OrderResponse, summary="Cambiar estado de la comanda")
def update_status(
    order_id: UUID, body: OrderStatusUpdate,
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
):
    order = get_or_404(db, CustomerOrder, order_id, "Order not found")
    order.status = body.status.value
    db.commit()
    return _load_order(db, order_id)
