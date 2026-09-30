"""spec 089 (A-94, data-model.md §1-§3): las cuatro columnas aditivas de los
adicionales cobrados una vez por línea.

Congela que los valores por defecto reproducen el comportamiento histórico: una
línea creada sin indicar `addons_total`/`per_line` (todas las anteriores al
despliegue y todas las de la terminal POS) devuelve exactamente el mismo total
que siempre (SC-003, Principios VII y VIII).
"""
import unittest
from decimal import Decimal

from app.characterization_tests import orders_fixtures as of
from app.models.cart_item import CartItem, CartItemOption
from app.models.order_item import OrderItem, OrderItemOption


class LineAddonsColumnDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.db = of.new_session()
        self.variant = of.make_variant(self.db, price=Decimal("18000"))

    def test_cart_item_addons_total_por_defecto_es_cero(self):
        cart = of.make_cart(self.db)
        item = of.make_cart_item(self.db, cart, self.variant, quantity=2)
        self.db.refresh(item)
        self.assertEqual(item.addons_total, Decimal("0"))

    def test_order_item_addons_total_por_defecto_es_cero(self):
        order = of.make_customer_order(self.db, of.make_table_session(self.db))
        item = of.make_order_item(self.db, order, self.variant, quantity=2)
        self.db.refresh(item)
        self.assertEqual(item.addons_total, Decimal("0"))

    def test_opciones_de_carrito_y_pedido_nacen_sin_per_line(self):
        group = of.make_option_group(self.db)
        opt = of.make_option(self.db, group=group, extra_price=Decimal("3000"))
        cart = of.make_cart(self.db)
        c_item = of.make_cart_item(self.db, cart, self.variant)
        c_opt = CartItemOption(cart_item_id=c_item.id, option_id=opt.id)
        order = of.make_customer_order(self.db, of.make_table_session(self.db))
        o_item = of.make_order_item(self.db, order, self.variant)
        o_opt = OrderItemOption(order_item_id=o_item.id, option_id=opt.id)
        self.db.add_all([c_opt, o_opt])
        self.db.flush()
        self.db.refresh(c_opt)
        self.db.refresh(o_opt)
        self.assertIs(c_opt.per_line, False)
        self.assertIs(o_opt.per_line, False)


class HistoricLineTotalTests(unittest.TestCase):
    """SC-003: una línea histórica (sin los campos nuevos) no cambia ni un centavo."""

    def setUp(self):
        self.db = of.new_session()
        self.variant = of.make_variant(self.db, price=Decimal("18000"))

    def test_linea_historica_line_total_es_unit_price_por_quantity(self):
        order = of.make_customer_order(self.db, of.make_table_session(self.db))
        item = of.make_order_item(self.db, order, self.variant, quantity=2)
        self.db.refresh(item)
        self.assertEqual(item.line_total, Decimal("36000"))

    def test_linea_nueva_suma_los_adicionales_una_sola_vez(self):
        order = of.make_customer_order(self.db, of.make_table_session(self.db))
        item = of.make_order_item(
            self.db, order, self.variant,
            quantity=2, unit_price=Decimal("15000"), addons_total=Decimal("3000"),
        )
        self.db.refresh(item)
        self.assertEqual(item.line_total, Decimal("33000"))


class AddonsTotalConstraintTests(unittest.TestCase):
    """`addons_total` negativo no es válido (CHECK). SQLite en memoria puede no
    aplicarlo, así que se comprueba también la declaración del constraint."""

    def _has_check(self, model, name: str) -> bool:
        # La convención de nombres del proyecto antepone `ck__<tabla>__` al nombre.
        return any(c.name and c.name.endswith(name) for c in model.__table__.constraints)

    def test_check_constraint_declarado_en_ambas_tablas(self):
        self.assertTrue(self._has_check(CartItem, "ck_cart_item_addons_total_nonneg"))
        self.assertTrue(self._has_check(OrderItem, "ck_order_item_addons_total_nonneg"))


if __name__ == "__main__":
    unittest.main()
