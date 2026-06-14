"""Repository layer for the products module.

Las clases son stateless: cada método recibe la `Session` activa (scoped al
schema del tenant por `get_db`). Encapsulan el acceso a datos para que el
service y las estrategias no construyan SQL directamente.
"""
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

from app.core.crud import get_or_404
from app.models.product import Product
from app.models.product_component import ProductComponent
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement


class ProductRepository:
    def get(self, db: Session, id: UUID) -> Product | None:
        return db.get(Product, id)

    def get_or_404(self, db: Session, id: UUID) -> Product:
        return get_or_404(db, Product, id, "Product not found")

    def get_with_components_or_404(self, db: Session, id: UUID) -> Product:
        """Producto con inventory y componentes (+ su producto) precargados."""
        product = db.execute(
            select(Product)
            .options(
                selectinload(Product.inventory),
                selectinload(Product.components).selectinload(ProductComponent.component),
            )
            .where(Product.id == id)
        ).scalar_one_or_none()
        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
            )
        return product

    def add(self, db: Session, product: Product) -> Product:
        db.add(product)
        db.flush()
        return product

    def exists(self, db: Session, id: UUID) -> bool:
        return db.get(Product, id) is not None

    def list_query(self, active: bool | None = None) -> Select:
        """Devuelve el `select` para paginar (sin ejecutar)."""
        stmt = (
            select(Product)
            .options(selectinload(Product.inventory))
            .order_by(Product.created_at.desc())
        )
        if active is not None:
            stmt = stmt.where(Product.active == active)
        return stmt


class ProductComponentRepository:
    def add(self, db: Session, component: ProductComponent) -> ProductComponent:
        db.add(component)
        db.flush()
        return component

    def list_by_product(self, db: Session, product_id: UUID) -> list[ProductComponent]:
        return list(
            db.execute(
                select(ProductComponent).where(ProductComponent.product_id == product_id)
            ).scalars()
        )

    def replace_for_product(
        self, db: Session, product_id: UUID, items: list[tuple[UUID, object]]
    ) -> None:
        """Borra los componentes actuales y crea los nuevos.

        `items` es una lista de tuplas `(component_id, quantity)`.
        """
        for existing in self.list_by_product(db, product_id):
            db.delete(existing)
        db.flush()
        for component_id, quantity in items:
            db.add(
                ProductComponent(
                    product_id=product_id,
                    component_id=component_id,
                    quantity=quantity,
                )
            )
        db.flush()


class InventoryRepository:
    def get_by_product(self, db: Session, product_id: UUID) -> Inventory | None:
        return db.execute(
            select(Inventory).where(Inventory.product_id == product_id)
        ).scalar_one_or_none()

    def create(
        self, db: Session, product_id: UUID, stock: int, stock_min: int
    ) -> Inventory:
        inventory = Inventory(stock=stock, stock_min=stock_min, product_id=product_id)
        db.add(inventory)
        db.flush()
        return inventory

    def add_initial_movement(self, db: Session, product_id: UUID, stock: int) -> None:
        """Registra el movimiento de 'Stock inicial' (income) si stock > 0."""
        if stock <= 0:
            return
        db.add(
            InventoryMovement(
                quantity=stock,
                stock_before=0,
                stock_after=stock,
                type_movement="income",
                reason="Stock inicial",
                product_id=product_id,
            )
        )
        db.flush()
