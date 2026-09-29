"""Numeración estable de pedidos de mesa — spec 087, FR-006 (A-86).

No son characterization tests en sentido estricto (no había comportamiento
previo que congelar: `table_order_number`/`cash_shift_id` son campos nuevos):
verifican el comportamiento nuevo introducido por esta spec en los dos puntos
de creación de `CustomerOrder` DINE_IN — `orders/service.py::create_order`
(mesero/staff) y `cart/service.py::submit_cart` (comensal vía Menú QR).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_orders_table_numbering -v
"""
from datetime import datetime
from decimal import Decimal
import unittest
from uuid import uuid4

from app.characterization_tests import orders_fixtures as fx
from app.characterization_tests import cart_fixtures
from app.api.v1.orders import service
from app.api.v1.orders.schemas import OrderChannel, OrderCreate, OrderItemIn, OrderType
from app.api.v1.cart import service as cart_service
from app.api.v1.cart.schemas import CartItemIn

PRECIO = Decimal("10000")


class TestOrdersTableNumbering(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_variant_con_receta(self, db):
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("1"))
        return variant

    def _crear_dine_in(self, db, table, variant):
        data = OrderCreate(
            channel=OrderChannel.POS,
            order_type=OrderType.DINE_IN,
            dining_table_id=table.id,
            customer_name="Cliente de prueba",
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1)],
        )
        return service.create_order(db, data, uuid4())

    # --------------------------------------------- create_order (mesero/staff)

    def test_dine_in_numera_consecutivo_y_estable_tras_un_cuarto_pedido(self):
        db = fx.new_session()
        shift = fx.make_cash_shift(db)
        table = fx.make_dining_table(db)
        variant = self._seed_variant_con_receta(db)
        db.commit()

        o1 = self._crear_dine_in(db, table, variant)
        db.commit()
        o2 = self._crear_dine_in(db, table, variant)
        db.commit()
        o3 = self._crear_dine_in(db, table, variant)
        db.commit()

        self.assertEqual(
            [o1.table_order_number, o2.table_order_number, o3.table_order_number], [1, 2, 3]
        )
        self.assertEqual({o1.cash_shift_id, o2.cash_shift_id, o3.cash_shift_id}, {shift.id})

        o4 = self._crear_dine_in(db, table, variant)
        db.commit()
        self.assertEqual(o4.table_order_number, 4)
        # Estables: los tres primeros no cambian al crear un cuarto.
        db.refresh(o1)
        db.refresh(o2)
        db.refresh(o3)
        self.assertEqual(
            [o1.table_order_number, o2.table_order_number, o3.table_order_number], [1, 2, 3]
        )

    def test_takeaway_no_recibe_numero(self):
        db = fx.new_session()
        fx.make_cash_shift(db)
        variant = self._seed_variant_con_receta(db)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.POS,
            order_type=OrderType.TAKEAWAY,
            customer_name="Cliente de prueba",
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1)],
        )
        order = service.create_order(db, data, uuid4())

        self.assertIsNone(order.table_order_number)
        self.assertIsNone(order.cash_shift_id)

    def test_delivery_no_recibe_numero(self):
        db = fx.new_session()
        fx.make_cash_shift(db)
        variant = self._seed_variant_con_receta(db)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.POS,
            order_type=OrderType.DELIVERY,
            customer_name="Cliente de prueba",
            delivery_address="Calle 1 # 2-3",
            delivery_fee=Decimal("2000"),
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1)],
        )
        order = service.create_order(db, data, uuid4())

        self.assertIsNone(order.table_order_number)
        self.assertIsNone(order.cash_shift_id)

    def test_sin_turno_abierto_no_bloquea_la_creacion_y_no_numera(self):
        db = fx.new_session()
        table = fx.make_dining_table(db)
        variant = self._seed_variant_con_receta(db)
        db.commit()

        order = self._crear_dine_in(db, table, variant)

        self.assertIsNone(order.table_order_number)
        self.assertIsNone(order.cash_shift_id)

    def test_cerrar_turno_y_abrir_uno_nuevo_reinicia_el_contador(self):
        db = fx.new_session()
        shift1 = fx.make_cash_shift(db)
        table = fx.make_dining_table(db)
        variant = self._seed_variant_con_receta(db)
        db.commit()

        o1 = self._crear_dine_in(db, table, variant)
        db.commit()
        o2 = self._crear_dine_in(db, table, variant)
        db.commit()
        self.assertEqual([o1.table_order_number, o2.table_order_number], [1, 2])

        shift1.status = "closed"
        shift1.closed_at = datetime.now()
        db.commit()

        shift2 = fx.make_cash_shift(db)
        db.commit()

        o3 = self._crear_dine_in(db, table, variant)
        db.commit()

        self.assertEqual(o3.table_order_number, 1)
        self.assertEqual(o3.cash_shift_id, shift2.id)

    # ------------------------------------------------- submit_cart (Menú QR)

    def test_submit_cart_dine_in_numera_igual_que_create_order(self):
        db = cart_fixtures.new_session()
        shift = cart_fixtures.make_cash_shift(db)
        table = cart_fixtures.make_dining_table(db)
        ts = cart_fixtures.make_table_session(db, table=table)
        category = cart_fixtures.make_category(db)
        product = cart_fixtures.make_product(db, category=category)
        variant = cart_fixtures.make_variant(db, product=product, price=PRECIO)
        efectivo = cart_fixtures.make_payment_method(db, is_cash=True)
        db.commit()

        participante1 = cart_fixtures.make_participant(db, table_session=ts)
        db.commit()
        cart_service.add_item(
            db, participante1.id, CartItemIn(product_variant_id=variant.id, quantity=1)
        )
        order1 = cart_service.submit_cart(db, participante1, efectivo.id)
        db.commit()

        participante2 = cart_fixtures.make_participant(db, table_session=ts)
        db.commit()
        cart_service.add_item(
            db, participante2.id, CartItemIn(product_variant_id=variant.id, quantity=1)
        )
        order2 = cart_service.submit_cart(db, participante2, efectivo.id)
        db.commit()

        self.assertEqual(order1.table_order_number, 1)
        self.assertEqual(order2.table_order_number, 2)
        self.assertEqual(order1.cash_shift_id, shift.id)
        self.assertEqual(order2.cash_shift_id, shift.id)


if __name__ == "__main__":
    unittest.main()
