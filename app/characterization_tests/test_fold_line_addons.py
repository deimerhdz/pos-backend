"""Tests de la nueva funcionalidad — spec 089 (A-94): `app/scripts/fold_line_addons.py`
(data-model.md §5 nivel 3): pliega los adicionales por línea de los carritos abiertos.

`with_db` se parchea para entregar sesiones SQLite en memoria (una por esquema, más la global con
`shared.tenants`); mismo patrón que `test_migrate_receipt_keys.py`.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_fold_line_addons -v
"""
import io
import unittest
import uuid
from contextlib import contextmanager, redirect_stdout
from decimal import Decimal
from unittest import mock

from app.characterization_tests import orders_fixtures as fx
from app.characterization_tests.test_migrate_receipt_keys import _new_session as _shared_session
from app.models.cart_item import CartItem, CartItemOption
from app.models.order_item import OrderItem
from app.scripts import fold_line_addons as fold


class TestFoldLineAddons(unittest.TestCase):
    def setUp(self):
        self.shared = _shared_session(tenants=["acme"])
        self.db = fx.new_session()
        self.sessions = {None: self.shared, "acme": self.db}

        @contextmanager
        def fake_with_db(schema):
            yield self.sessions[schema]

        patcher = mock.patch.object(fold, "with_db", fake_with_db)
        patcher.start()
        self.addCleanup(patcher.stop)

        variant = fx.make_variant(self.db, price=Decimal("15000"))
        group = fx.make_option_group(self.db)
        self.tocino = fx.make_option(self.db, group=group, extra_price=Decimal("3000"))
        cart = fx.make_cart(self.db)
        # Exacta: 2 × 15.000 + 3.000 a repartir → unit_price 16.500 (3.000 / 2 = 1.500).
        self.exacta = self._line(cart, variant, quantity=2, addons=Decimal("3000"))
        # No exacta: 3 unidades y 1.000 → 333,33 × 3 = 999,99 ≠ 1.000.
        self.no_exacta = self._line(cart, variant, quantity=3, addons=Decimal("1000"))
        # Sin adicionales: no se toca.
        self.historica = self._line(cart, variant, quantity=2, addons=Decimal("0"),
                                    unit_price=Decimal("18000"))
        self.db.commit()

    def _line(self, cart, variant, *, quantity, addons, unit_price=Decimal("15000")):
        item = fx.make_cart_item(
            self.db, cart, variant, quantity=quantity, unit_price=unit_price, addons_total=addons
        )
        self.db.add(CartItemOption(
            cart_item_id=item.id, option_id=self.tocino.id, quantity=1,
            per_line=addons > 0,
        ))
        self.db.flush()
        return item

    def _run(self, **kwargs):
        out = io.StringIO()
        with redirect_stdout(out):
            code = fold.run(**kwargs)
        return code, out.getvalue()

    def _reload(self, item):
        self.db.expire_all()
        return self.db.get(CartItem, item.id)

    def test_simulacion_por_defecto_lista_sin_escribir(self):
        code, out = self._run(write=False)
        self.assertEqual(code, 0)
        self.assertIn("SIMULACIÓN", out)
        self.assertIn("1 a plegar", out)
        self.assertIn("1 no exactas", out)
        self.assertEqual(self._reload(self.exacta).addons_total, Decimal("3000"))
        self.assertEqual(self._reload(self.exacta).unit_price, Decimal("15000"))

    def test_apply_pliega_solo_cuando_la_division_es_exacta(self):
        code, _ = self._run(write=True)
        self.assertEqual(code, 0)
        exacta = self._reload(self.exacta)
        self.assertEqual(exacta.unit_price, Decimal("16500"))
        self.assertEqual(exacta.addons_total, Decimal("0"))
        self.assertTrue(all(o.per_line is False for o in exacta.options))
        # El total de la línea no cambia: 2 × 15.000 + 3.000 == 2 × 16.500.
        self.assertEqual(exacta.unit_price * exacta.quantity, Decimal("33000"))

    def test_las_no_exactas_se_listan_y_no_se_tocan(self):
        _, out = self._run(write=True)
        no_exacta = self._reload(self.no_exacta)
        self.assertEqual(no_exacta.unit_price, Decimal("15000"))
        self.assertEqual(no_exacta.addons_total, Decimal("1000"))
        self.assertTrue(no_exacta.options[0].per_line)
        self.assertIn(str(self.no_exacta.id), out)

    def test_una_linea_sin_adicionales_no_se_toca(self):
        self._run(write=True)
        self.assertEqual(self._reload(self.historica).unit_price, Decimal("18000"))

    def test_es_idempotente(self):
        self._run(write=True)
        _, out = self._run(write=True)
        self.assertIn("0 a plegar", out)
        self.assertEqual(self._reload(self.exacta).unit_price, Decimal("16500"))

    def test_nunca_modifica_order_items(self):
        order = fx.make_customer_order(self.db, fx.make_table_session(self.db))
        variant = fx.make_variant(self.db, price=Decimal("15000"))
        item = fx.make_order_item(
            self.db, order, variant, quantity=2, unit_price=Decimal("15000"),
            addons_total=Decimal("3000"),
        )
        self.db.commit()
        self._run(write=True)
        self.db.expire_all()
        row = self.db.get(OrderItem, item.id)
        self.assertEqual(row.addons_total, Decimal("3000"))
        self.assertEqual(row.unit_price, Decimal("15000"))

    def test_tenant_inexistente_falla_con_codigo_1(self):
        code, _ = self._run(write=False, tenant="no-existe")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
