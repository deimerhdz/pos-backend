"""Service de productos (catálogo simple para heladería).

Un producto pertenece a una categoría y tiene 1..N variantes vendibles (precio +
receta viven en la variante). Al crear un producto se le da una variante default
'Single' para que sea vendible de inmediato; se agregan más desde el módulo catalog.
"""
import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.core.crud import get_or_404
from app.core.storage import delete_object, key_from_public_url
from app.models.product import Product
from app.models.category import Category
from app.api.v1.catalog.service import ensure_default_variant
from app.api.v1.products.schemas import ProductCreate, ProductUpdate

logger = logging.getLogger(__name__)


class ProductService:
    def _validate_fks(self, db: Session, category_id: UUID | None) -> None:
        if category_id is not None:
            get_or_404(db, Category, category_id, "Category not found")

    def list_query(self, active: bool | None = None) -> Select:
        stmt = select(Product).order_by(Product.created_at.desc())
        if active is not None:
            stmt = stmt.where(Product.active == active)
        return stmt

    def get_or_404(self, db: Session, id: UUID) -> Product:
        return get_or_404(db, Product, id, "Product not found")

    def create_product(self, db: Session, data: ProductCreate) -> Product:
        self._validate_fks(db, data.category_id)
        try:
            product = Product(
                category_id=data.category_id,
                name=data.name,
                description=data.description,
                preparation_type=data.preparation_type.value,
                image_url=data.image_url,
            )
            db.add(product)
            db.flush()
            # Todo vendible es una variante; el producto nace con su default 'Single'.
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
        self._validate_fks(db, data.category_id)
        if data.category_id is not None:
            product.category_id = data.category_id
        if data.name is not None:
            product.name = data.name
        if data.description is not None:
            product.description = data.description
        if data.preparation_type is not None:
            product.preparation_type = data.preparation_type.value
        if data.image_url is not None and data.image_url != product.image_url:
            old_image_url = product.image_url
            product.image_url = data.image_url
            if old_image_url:
                old_key = key_from_public_url(old_image_url)
                if old_key:
                    delete_object(old_key)
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
