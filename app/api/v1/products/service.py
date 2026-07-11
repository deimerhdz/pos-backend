"""Service de productos: creación/actualización directa del catálogo.

Tras el cutover (Fase 3) el producto es una entidad de catálogo (SIMPLE o
CONFIGURABLE); el inventario vive en `Supply` y la receta en `Recipe`. Un producto
SIMPLE recibe una variante default para poder venderse.
"""
import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.core.crud import get_or_404
from app.models.product import Product
from app.models.category import Category
from app.models.unit_measure import UnitMeasure
from app.api.v1.catalog.service import ensure_default_variant
from app.api.v1.products.schemas import ProductCreate, ProductUpdate

logger = logging.getLogger(__name__)


class ProductService:
    def _validate_fks(
        self, db: Session, category_id: UUID | None, unit_measure_id: UUID | None
    ) -> None:
        if category_id is not None:
            get_or_404(db, Category, category_id, "Category not found")
        if unit_measure_id is not None:
            get_or_404(db, UnitMeasure, unit_measure_id, "Unit measure not found")

    def list_query(self, active: bool | None = None) -> Select:
        stmt = select(Product).order_by(Product.created_at.desc())
        if active is not None:
            stmt = stmt.where(Product.active == active)
        return stmt

    def get_or_404(self, db: Session, id: UUID) -> Product:
        return get_or_404(db, Product, id, "Product not found")

    def create_product(self, db: Session, data: ProductCreate) -> Product:
        self._validate_fks(db, data.category_id, data.unit_measure_id)
        try:
            product = Product(
                name=data.name,
                description=data.description,
                type=data.type.value,
                price=data.price,
                cost=data.cost,
                is_menu=data.is_menu,
                image_url=data.image_url,
                category_id=data.category_id,
                unit_measure_id=data.unit_measure_id,
            )
            db.add(product)
            db.flush()
            # Todo vendible es una variante; SIMPLE recibe su variante default.
            if product.type == "SIMPLE":
                ensure_default_variant(db, product)
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
        product = self.get_or_404(db, id)
        self._validate_fks(db, data.category_id, data.unit_measure_id)
        if data.name is not None:
            product.name = data.name
        if data.description is not None:
            product.description = data.description
        if data.type is not None:
            product.type = data.type.value
        if data.price is not None:
            product.price = data.price
        if data.cost is not None:
            product.cost = data.cost
        if data.is_menu is not None:
            product.is_menu = data.is_menu
        if data.image_url is not None:
            product.image_url = data.image_url
        if data.category_id is not None:
            product.category_id = data.category_id
        if data.unit_measure_id is not None:
            product.unit_measure_id = data.unit_measure_id
        if data.active is not None:
            product.active = data.active
        db.commit()
        db.refresh(product)
        return product

    def soft_delete(self, db: Session, id: UUID) -> Product:
        product = self.get_or_404(db, id)
        product.active = False
        db.commit()
        db.refresh(product)
        return product
