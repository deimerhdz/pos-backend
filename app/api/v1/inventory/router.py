from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.db import get_db
from app.core.dependencies import get_current_user
from app.core.crud import get_or_404
from app.core.models import User
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement
from app.api.v1.inventory.schemas import InventoryMovementCreate, InventoryMovementResponse

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _apply_movement(
    db: Session,
    product_id: UUID,
    body: InventoryMovementCreate,
    type_movement: str,
) -> InventoryMovement:
    get_or_404(db, Product, product_id, "Product not found")

    inventory = db.execute(
        select(Inventory).where(Inventory.product_id == product_id)
    ).scalar_one_or_none()
    if inventory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory not found")

    stock_before = inventory.stock
    if type_movement == "income":
        stock_after = stock_before + body.quantity
    else:  # expense
        if body.quantity > stock_before:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient stock",
            )
        stock_after = stock_before - body.quantity

    inventory.stock = stock_after

    movement = InventoryMovement(
        quantity=body.quantity,
        stock_before=stock_before,
        stock_after=stock_after,
        type_movement=type_movement,
        reference_id=body.reference_id,
        reason=body.reason,
        product_id=product_id,
    )
    db.add(movement)
    db.commit()
    db.refresh(movement)
    return movement


@router.post(
    "/{product_id}/income",
    response_model=InventoryMovementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar entrada de stock",
    description="Registra una entrada (income) de inventario para el producto indicado, incrementando su stock y dejando registro del movimiento.",
    response_description="El movimiento de inventario registrado.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "El producto o su inventario no existen."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def register_income(
    product_id: UUID,
    body: InventoryMovementCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return _apply_movement(db, product_id, body, "income")


@router.post(
    "/{product_id}/expense",
    response_model=InventoryMovementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar salida de stock",
    description="Registra una salida (expense) de inventario para el producto indicado, descontando su stock. Falla si la cantidad supera el stock disponible.",
    response_description="El movimiento de inventario registrado.",
    responses={
        400: {"description": "Stock insuficiente para realizar la salida."},
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "El producto o su inventario no existen."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def register_expense(
    product_id: UUID,
    body: InventoryMovementCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return _apply_movement(db, product_id, body, "expense")
