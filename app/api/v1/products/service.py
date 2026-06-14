"""Service Layer: orquesta factory + estrategias + repositorios.

Es el dueño de la transacción: las estrategias y repositorios solo hacen
`add`/`flush`; aquí se hace `commit`/`rollback`. Mantiene la lógica de negocio
fuera de la capa HTTP (router).
"""
import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.crud import get_or_404
from app.models.product import Product
from app.models.category import Category
from app.models.unit_measure import UnitMeasure
from app.api.v1.products.repositories import (
    ProductRepository,
    ProductComponentRepository,
    InventoryRepository,
)
from app.api.v1.products.factory import StrategyFactory
from app.api.v1.products.schemas import ProductCreate, ProductUpdate

logger = logging.getLogger(__name__)


class ProductService:
    def __init__(self) -> None:
        self.products = ProductRepository()
        self.components = ProductComponentRepository()
        self.inventory = InventoryRepository()

    # --- validaciones compartidas ---

    def _validate_fks(
        self, db: Session, category_id: UUID | None, unit_measure_id: UUID | None
    ) -> None:
        if category_id is not None:
            get_or_404(db, Category, category_id, "Category not found")
        if unit_measure_id is not None:
            get_or_404(db, UnitMeasure, unit_measure_id, "Unit measure not found")

    def _strategy_for(self, product_type: str):
        return StrategyFactory.resolve(
            product_type, self.products, self.components, self.inventory
        )

    # --- casos de uso ---

    def create_product(self, db: Session, data: ProductCreate) -> Product:
        self._validate_fks(db, data.category_id, data.unit_measure_id)
        strategy = self._strategy_for(data.product_type.value)
        try:
            product = strategy.create(db, data)
            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.exception("Error creando producto")
            raise
        db.refresh(product)
        return product

    def update_product(self, db: Session, id: UUID, data: ProductUpdate) -> Product:
        product = self.products.get_or_404(db, id)
        self._validate_fks(db, data.category_id, data.unit_measure_id)
        # Si se envía product_type, se aplica la estrategia del tipo destino (permite
        # cambiar el tipo); si no, se mantiene la del tipo actual.
        target_type = (
            data.product_type.value if data.product_type is not None else product.product_type
        )
        strategy = self._strategy_for(target_type)
        try:
            product = strategy.update(db, product, data)
            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.exception("Error actualizando producto %s", id)
            raise
        db.refresh(product)
        return product

    def soft_delete(self, db: Session, id: UUID) -> Product:
        product = self.products.get_or_404(db, id)
        product.active = False
        db.commit()
        db.refresh(product)
        return product
