"""CONGELA comportamiento actual: `create_order`, la única función pública de
`app/api/v1/orders/service.py` (specs/017-caracterizacion-orders, Historia 5)
— la comanda directa de mostrador/mesero que nace ya `abierta` (confirmada) y
descuenta inventario al crearse, porque no vuelve a pasar por `confirm_order`.

El caso de contraste directo con **A-04** (`create_order` sí pasa `variant` a
`load_valid_options`, a diferencia de `consolidation.add_item_to_table`) ya
vive en `test_orders_consolidation.py::test_create_order_contraste_a04_...`
(tasks.md T013/T051: se escribe una sola vez).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_orders_service -v
"""
from decimal import Decimal
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.orders import service
from app.api.v1.orders.schemas import OrderChannel, OrderCreate, OrderItemIn
from app.models.inventory_movement import InventoryMovement

PRECIO = Decimal("10000")


class TestService(unittest.TestCase):
    def _seed_variant_con_receta_y_opciones(self, db):
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("2"))
        group = fx.make_option_group(db, min_select=1, max_select=1)
        option = fx.make_option(db, group=group, extra_price=Decimal("500"))
        fx.link_variant_group(db, variant, group, min_select=1, max_select=1)
        return variant, insumo, option

    # ------------------------------------------------------------ create_order (T048)

    def test_create_order_nace_abierta_y_descuenta_inventario(self):
        """CONGELA comportamiento actual (`service.py:37+`, spec.md Historia
        5, escenario 1): variante con receta y opciones válidas →
        `create_order` nace en status 'abierta' (no 'recibida') con el
        inventario ya descontado — no vuelve a pasar por `confirm_order`."""
        db = fx.new_session()
        variant, insumo, option = self._seed_variant_con_receta_y_opciones(db)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.COUNTER,
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1, option_ids=[option.id])],
        )
        order = service.create_order(db, data, uuid4())

        self.assertEqual(order.status, "abierta")
        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 1)
        db.refresh(insumo)
        self.assertEqual(Decimal(insumo.current_stock), Decimal("998"))

    # ----------------------------------------------- create_order sin receta (T049)

    def test_create_order_variante_sin_receta_rechaza(self):
        """CONGELA comportamiento actual: variante sin receta asociada → la
        guarda de `deduct_order_items` rechaza la creación con 409, migrando
        el caso de `test_receta_obligatoria.py` correspondiente a este
        camino (research.md §5, SC-007)."""
        db = fx.new_session()
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant_sin_receta = fx.make_variant(db, product=product, price=PRECIO)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.COUNTER,
            items=[OrderItemIn(product_variant_id=variant_sin_receta.id, quantity=1)],
        )
        with self.assertRaises(HTTPException) as ctx:
            service.create_order(db, data, uuid4())
        self.assertEqual(ctx.exception.status_code, 409)

    # ------------------------------------------- create_order abre sesión (T050)

    def test_create_order_abre_sesion_de_mesa_si_no_existe(self):
        """CONGELA comportamiento actual (spec.md Historia 5, escenario 3):
        mesa sin sesión de mesa activa → `create_order` con
        `dining_table_id` crea la sesión de mesa vía
        `consolidation.get_or_create_table_session_id` antes de crear la
        orden — congelando la dependencia real entre `service.py` y
        `consolidation.py`."""
        db = fx.new_session()
        table = fx.make_dining_table(db, status="libre")
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("1"))
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.WAITER,
            dining_table_id=table.id,
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1)],
        )
        order = service.create_order(db, data, uuid4())

        self.assertIsNotNone(order.table_session_id)
        db.refresh(table)
        self.assertEqual(table.status, "ocupada")


if __name__ == "__main__":
    unittest.main()
