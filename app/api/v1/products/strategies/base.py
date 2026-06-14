"""Strategy Pattern: comportamiento de creación/actualización por tipo de producto.

`ProductStrategy` define el contrato. Las subclases encapsulan las diferencias por
tipo (inventario / componentes) SIN condicionales `if product.type == ...`.
La base concentra la lógica común de armado/actualización de la fila `products`.
"""
from abc import ABC, abstractmethod
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.product import Product
from app.api.v1.products.repositories import (
    ProductRepository,
    ProductComponentRepository,
    InventoryRepository,
)
from app.api.v1.products.schemas import ProductCreate, ProductUpdate


class ProductStrategy(ABC):
    """Estrategia base. Recibe los repositorios inyectados por la factory."""

    def __init__(
        self,
        product_repo: ProductRepository,
        component_repo: ProductComponentRepository,
        inventory_repo: InventoryRepository,
    ) -> None:
        self.products = product_repo
        self.components = component_repo
        self.inventory = inventory_repo

    @abstractmethod
    def create(self, db: Session, data: ProductCreate) -> Product:
        ...

    @abstractmethod
    def update(self, db: Session, product: Product, data: ProductUpdate) -> Product:
        ...

    # --- helpers compartidos (no dependen del tipo) ---

    def _build_product(self, db: Session, data: ProductCreate) -> Product:
        """Crea y persiste la fila base de `products` con los campos comunes."""
        product = Product(
            name=data.name,
            description=data.description,
            price=data.price,
            cost=data.cost,
            is_menu=data.is_menu,
            product_type=data.product_type.value,
            control_stock=data.control_stock,
            category_id=data.category_id,
            unit_measure_id=data.unit_measure_id,
        )
        return self.products.add(db=db, product=product)

    def _apply_basic_updates(self, product: Product, data: ProductUpdate) -> None:
        """Aplica los campos básicos enviados (parcial) a un producto existente."""
        if data.name is not None:
            product.name = data.name
        if data.description is not None:
            product.description = data.description
        if data.price is not None:
            product.price = data.price
        if data.cost is not None:
            product.cost = data.cost
        if data.is_menu is not None:
            product.is_menu = data.is_menu
        if data.product_type is not None:
            product.product_type = data.product_type.value
        if data.control_stock is not None:
            product.control_stock = data.control_stock
        if data.category_id is not None:
            product.category_id = data.category_id
        if data.unit_measure_id is not None:
            product.unit_measure_id = data.unit_measure_id
        if data.active is not None:
            product.active = data.active
