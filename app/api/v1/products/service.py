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
from app.core.models import Tenant
from app.core.plan_limits import ensure_module_access
from app.core.storage import delete_object, key_from_public_url
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.category import Category
from app.api.v1.catalog.service import (
    ensure_default_variant,
    _save_variant_entry,
    _assign_display_orders,
)
from app.api.v1.catalog.schemas import VariantSaveIn
from app.api.v1.products.schemas import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductSaveResponse,
    VariantSaveOut,
    RecipeItemResponse,
    VariantOptionGroupResponse,
)

logger = logging.getLogger(__name__)


class ProductService:
    def _validate_fks(self, db: Session, category_id: UUID | None) -> None:
        if category_id is not None:
            get_or_404(db, Category, category_id, "Category not found")

    def list_query(self, active: bool | None = None, search: str | None = None) -> Select:
        stmt = select(Product).order_by(Product.created_at.desc())
        if active is not None:
            stmt = stmt.where(Product.active == active)
        if search:
            stmt = stmt.where(Product.name.ilike(f"%{search.strip()}%"))
        return stmt

    def get_or_404(self, db: Session, id: UUID) -> Product:
        return get_or_404(db, Product, id, "Product not found")

    def create_product(self, db: Session, tenant: Tenant, data: ProductCreate) -> Product:
        self._validate_fks(db, data.category_id)
        # spec 064, FR-011/FR-012: activar "maneja inventario" exige el módulo Inventario
        # en el plan vigente del tenant -- gating a nivel de campo (research.md Decisión 4):
        # crear un producto con tracks_inventory=False sigue funcionando sin ese módulo.
        if data.tracks_inventory:
            ensure_module_access(db, tenant, "inventario")
        try:
            product = Product(
                category_id=data.category_id,
                name=data.name,
                description=data.description,
                preparation_type=data.preparation_type.value,
                image_url=data.image_url,
                available=data.available,
                tracks_inventory=data.tracks_inventory,
            )
            db.add(product)
            db.flush()
            if data.variants:
                # spec 043 (FR-001): árbol completo en la misma transacción --
                # `ensure_default_variant` no aplica, ya hay al menos una presentación explícita.
                self._save_variant_tree(db, product, data.variants)
            else:
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

    def update_product(self, db: Session, tenant: Tenant, id: UUID, data: ProductUpdate) -> Product:
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
        old_key = None
        if data.image_url is not None and data.image_url != product.image_url:
            old_image_url = product.image_url
            product.image_url = data.image_url
            if old_image_url:
                old_key = key_from_public_url(old_image_url)
        if data.active is not None:
            product.active = data.active
        if data.available is not None:
            product.available = data.available
        if data.tracks_inventory is not None:
            # spec 064, FR-011/FR-012: solo reevalúa el plan cuando el valor realmente
            # cambia a `True` -- un PATCH que no toca este campo, o que lo deja igual,
            # nunca dispara un 403 sorpresivo por otra edición no relacionada.
            if data.tracks_inventory and not product.tracks_inventory:
                ensure_module_access(db, tenant, "inventario")
            product.tracks_inventory = data.tracks_inventory

        try:
            # spec 043 (FR-002): `variants` ausente del body = no tocar ninguna presentación
            # (back-compat); presente (incluida `[]`) = reconciliación completa. Todo esto entra
            # en el mismo `db.commit()` de abajo que ya persistía los campos del producto --
            # ningún cambio de esta llamada se guarda si la reconciliación falla (FR-004).
            if "variants" in data.model_fields_set:
                self._reconcile_variants(db, product, data.variants or [])
            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.exception("Error actualizando producto")
            raise
        db.refresh(product)
        if old_key:
            delete_object(old_key)
        return product

    def soft_delete(self, db: Session, id: UUID) -> Product:
        product = self.get_or_404(db, id)
        product.active = False
        db.commit()
        db.refresh(product)
        return product

    # ===================== Guardado consolidado (spec 043) =====================

    def _save_variant_tree(
        self, db: Session, product: Product, entries: list[VariantSaveIn]
    ) -> None:
        """Crea todas las presentaciones iniciales de un producto nuevo, en el orden recibido
        (FR-001). No hace `commit()` -- lo controla `create_product`."""
        variants = [
            _save_variant_entry(db, product, entry, index, {})
            for index, entry in enumerate(entries)
        ]
        _assign_display_orders(db, product.id, variants)

    def _reconcile_variants(
        self, db: Session, product: Product, entries: list[VariantSaveIn]
    ) -> None:
        """Reconcilia el conjunto de presentaciones activas del producto contra `entries` (spec
        043, FR-002, data-model.md tabla de reconciliación): las entradas sin `id` se crean, las
        que traen `id` se actualizan (incluida una presentación **inactiva** -- así se reactiva,
        `RN-CAT-09`), y cualquier presentación activa existente que `entries` no mencione se
        desactiva (`RN-CAT-10`). `display_order` queda según la posición de cada entrada dentro de
        `entries`. No hace `commit()` -- lo controla `update_product`.
        """
        all_existing = db.execute(
            select(ProductVariant).where(ProductVariant.product_id == product.id)
        ).scalars().all()
        existing_by_id = {v.id: v for v in all_existing}
        kept_ids = {entry.id for entry in entries if entry.id is not None}

        for variant in all_existing:
            if variant.active and variant.id not in kept_ids:
                variant.active = False

        resolved = [
            _save_variant_entry(db, product, entry, index, existing_by_id)
            for index, entry in enumerate(entries)
        ]
        _assign_display_orders(db, product.id, resolved)

    def to_save_response(self, product: Product) -> ProductSaveResponse:
        """Arma la respuesta completa de `POST`/`PATCH`/`PUT /products` (FR-006): el producto más
        sus presentaciones activas (`product.variants` ya viene ordenado por `display_order`,
        spec 042), cada una con su receta y sus grupos de opciones resueltos -- no se puede confiar
        en el mapeo automático de Pydantic porque el modelo ORM expone `recipe_items`, no
        `recipe`."""
        base = ProductResponse.model_validate(product)
        return ProductSaveResponse(
            **base.model_dump(),
            variants=[
                VariantSaveOut(
                    id=v.id,
                    product_id=v.product_id,
                    name=v.name,
                    sku=v.sku,
                    price=v.price,
                    active=v.active,
                    display_order=v.display_order,
                    recipe=[RecipeItemResponse.model_validate(r) for r in v.recipe_items],
                    option_groups=[
                        VariantOptionGroupResponse.model_validate(g) for g in v.option_groups
                    ],
                )
                for v in product.variants
                if v.active
            ],
        )
