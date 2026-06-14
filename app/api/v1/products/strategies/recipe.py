"""Estrategia para productos tipo RECIPE (compuesto / BOM).

No crea inventario: solo persiste la definición de consumo en `product_component`.
"""
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.product import Product
from app.api.v1.products.schemas import ProductCreate, ProductUpdate, ProductComponentIn
from app.api.v1.products.strategies.base import ProductStrategy


class RecipeStrategy(ProductStrategy):
    def create(self, db: Session, data: ProductCreate) -> Product:
        items = self._validate_components(db, data.components, product_id=None)
        product = self._build_product(db, data)
        # Una receta nunca gestiona inventario directo.
        product.control_stock = False
        self.components.replace_for_product(db=db, product_id=product.id, items=items)
        return product

    def update(self, db: Session, product: Product, data: ProductUpdate) -> Product:
        self._apply_basic_updates(product, data)
        product.control_stock = False
        if data.components is not None:
            items = self._validate_components(db, data.components, product_id=product.id)
            self.components.replace_for_product(
                db=db, product_id=product.id, items=items
            )
        elif not self.components.list_by_product(db, product.id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Una receta (RECIPE) requiere al menos un componente.",
            )
        return product

    # --- validación de consistencia de componentes ---

    def _validate_components(
        self,
        db: Session,
        components: list[ProductComponentIn] | None,
        product_id: UUID | None,
    ) -> list[tuple[UUID, object]]:
        if not components:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Una receta (RECIPE) requiere al menos un componente.",
            )

        seen: set[UUID] = set()
        items: list[tuple[UUID, object]] = []
        for comp in components:
            if product_id is not None and comp.component_id == product_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Una receta no puede contenerse a sí misma como componente.",
                )
            if comp.component_id in seen:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Componente duplicado: {comp.component_id}.",
                )
            if not self.products.exists(db, comp.component_id):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"El producto componente {comp.component_id} no existe.",
                )
            seen.add(comp.component_id)
            items.append((comp.component_id, comp.quantity))
        return items
