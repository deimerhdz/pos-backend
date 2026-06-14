"""Factory Pattern: resuelve la estrategia según el tipo de producto.

Es el ÚNICO punto que mapea tipo → comportamiento, mediante un registro
(diccionario), no condicionales. Añadir un nuevo tipo (COMBO/BUNDLE) solo requiere
una nueva estrategia y `StrategyFactory.register(...)`.
"""
from fastapi import HTTPException, status

from app.api.v1.products.repositories import (
    ProductRepository,
    ProductComponentRepository,
    InventoryRepository,
)
from app.api.v1.products.strategies import (
    ProductStrategy,
    IngredientStrategy,
    RecipeStrategy,
    SimpleProductStrategy,
)


class StrategyFactory:
    _registry: dict[str, type[ProductStrategy]] = {
        "INGREDIENT": IngredientStrategy,
        "RECIPE": RecipeStrategy,
        "PRODUCT": SimpleProductStrategy,
    }

    @classmethod
    def register(cls, product_type: str, strategy_cls: type[ProductStrategy]) -> None:
        """Registra una estrategia para un nuevo tipo (extensibilidad: COMBO/BUNDLE)."""
        cls._registry[product_type] = strategy_cls

    @classmethod
    def resolve(
        cls,
        product_type: str,
        product_repo: ProductRepository,
        component_repo: ProductComponentRepository,
        inventory_repo: InventoryRepository,
    ) -> ProductStrategy:
        strategy_cls = cls._registry.get(product_type)
        if strategy_cls is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Tipo de producto no soportado: {product_type}.",
            )
        return strategy_cls(product_repo, component_repo, inventory_repo)
