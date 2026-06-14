"""Estrategia para productos tipo INGREDIENT (materia prima con stock directo)."""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.product import Product
from app.api.v1.products.schemas import ProductCreate, ProductUpdate
from app.api.v1.products.strategies.base import ProductStrategy


class IngredientStrategy(ProductStrategy):
    def create(self, db: Session, data: ProductCreate) -> Product:
        if data.stock is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="stock es obligatorio para productos tipo INGREDIENT.",
            )
        product = self._build_product(db, data)
        self.inventory.create(
            db=db, product_id=product.id, stock=data.stock, stock_min=data.stock_min
        )
        self.inventory.add_initial_movement(db=db, product_id=product.id, stock=data.stock)
        return product

    def update(self, db: Session, product: Product, data: ProductUpdate) -> Product:
        self._apply_basic_updates(product, data)
        if data.stock_min is not None:
            inventory = self.inventory.get_by_product(db, product.id)
            if inventory is not None:
                inventory.stock_min = data.stock_min
        return product
