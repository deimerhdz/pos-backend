"""Tests de la nueva funcionalidad — spec 024-pagos-ordenes-mesa, User Story
6: un participante solo puede tener una orden activa a la vez (FR-005/FR-006).

No es un characterization test: `submit_cart` no valida esto hoy (research.md
spec 024, Decisión 8) — es una restricción nueva.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_cart_single_active_order -v
"""
from decimal import Decimal
import unittest

from fastapi import HTTPException

from app.characterization_tests import cart_fixtures as fx
from app.api.v1.cart import service


class TestCartSingleActiveOrder(unittest.TestCase):
    def _seed_session_con_carrito(self):
        db = fx.new_session()
        table = fx.make_dining_table(db)
        ts = fx.make_table_session(db, table=table)
        participant = fx.make_participant(db, table_session=ts)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=Decimal("8000"))
        cart = fx.make_cart(db, participant=participant)
        fx.make_cart_item(db, cart, variant)
        db.commit()
        return db, participant, variant

    def test_segunda_orden_con_la_primera_pendiente_falla_409(self):
        """Acceptance Scenario 1 (US6)."""
        db, participant, variant = self._seed_session_con_carrito()
        first = service.submit_cart(db, participant)
        self.assertEqual(first.status, "recibida")

        # Nuevo carrito abierto tras el submit (mismo patrón que
        # `_get_or_create_open_cart`); se le agrega un ítem para poder
        # reintentar el envío.
        cart2 = fx.make_cart(db, participant=participant)
        fx.make_cart_item(db, cart2, variant)
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            service.submit_cart(db, participant)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_segunda_orden_tras_finalizar_la_primera_se_permite(self):
        """Acceptance Scenario 2 (US6): 'finalizada' = pagada o cancelada
        (research.md spec 024, Decisión 8)."""
        db, participant, variant = self._seed_session_con_carrito()
        first = service.submit_cart(db, participant)
        first.status = "pagada"
        db.commit()

        cart2 = fx.make_cart(db, participant=participant)
        fx.make_cart_item(db, cart2, variant)
        db.commit()

        second = service.submit_cart(db, participant)
        self.assertNotEqual(second.id, first.id)
        self.assertEqual(second.status, "recibida")

    def test_segunda_orden_tras_cancelar_la_primera_se_permite(self):
        db, participant, variant = self._seed_session_con_carrito()
        first = service.submit_cart(db, participant)
        first.status = "cancelada"
        db.commit()

        cart2 = fx.make_cart(db, participant=participant)
        fx.make_cart_item(db, cart2, variant)
        db.commit()

        second = service.submit_cart(db, participant)
        self.assertEqual(second.status, "recibida")


if __name__ == "__main__":
    unittest.main()
