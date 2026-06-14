"""Inyección de dependencias del módulo de productos."""
from app.api.v1.products.service import ProductService


def get_product_service() -> ProductService:
    """Provee el ProductService. Los repos son stateless, una instancia por request."""
    return ProductService()
