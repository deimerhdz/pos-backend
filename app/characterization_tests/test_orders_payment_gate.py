"""Tests de la nueva funcionalidad — spec 024-pagos-ordenes-mesa, User Stories
2 (revisión de comprobante), 3 (confirmación de efectivo) y 4 (gate de
comanda): FR-009/FR-010/FR-010a/FR-013/FR-014/FR-017/FR-018.

No son characterization tests: la revisión de comprobantes, la confirmación de
efectivo por intento y el gate de `confirm_order` son comportamiento nuevo
(research.md spec 024, Decisiones 5 y 9) — se verifican contra
`spec.md`/`contracts/cashier-payment-review.md`/`contracts/order-confirm-gate.md`,
no contra un comportamiento heredado.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_orders_payment_gate -v
"""
from decimal import Decimal
import unittest

from fastapi import HTTPException

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.orders import checkout

PRECIO = Decimal("18000")


class TestOrdersPaymentGate(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_order_recibida(self, *, precio=PRECIO):
        db = fx.new_session()
        table = fx.make_dining_table(db)
        ts = fx.make_table_session(db, table=table)
        participant = fx.make_participant(db, table_session=ts)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=precio)
        order = fx.make_customer_order(db, ts, participant=participant, status="recibida")
        fx.make_order_item(db, order, variant, estado_cocina="pendiente")
        db.commit()
        return db, order

    def _user(self):
        return fx.make_user_double()

    # ------------------------------------------------- approve/reject (US2)

    def test_approve_confirma_intento_de_transferencia(self):
        """Acceptance Scenario 4 (US2)."""
        db, order = self._seed_order_recibida()
        nequi = fx.make_payment_method(db, name="Nequi", is_cash=False, type="transfer")
        attempt = fx.make_payment_attempt(
            db, order, nequi, status="pendiente", receipt_file_url="https://example.invalid/a.jpg"
        )
        db.commit()

        result = checkout.approve_payment_attempt(db, attempt.id, self._user())
        self.assertEqual(result.status, "confirmado")
        self.assertIsNotNone(result.resolved_at)

    def test_approve_409_sin_comprobante_todavia(self):
        db, order = self._seed_order_recibida()
        nequi = fx.make_payment_method(db, name="Nequi", is_cash=False, type="transfer")
        attempt = fx.make_payment_attempt(db, order, nequi, status="pendiente")
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.approve_payment_attempt(db, attempt.id, self._user())
        self.assertEqual(ctx.exception.status_code, 409)

    def test_reject_exige_motivo_y_lo_registra(self):
        """FR-014, Acceptance Scenario 6 (US2)."""
        db, order = self._seed_order_recibida()
        nequi = fx.make_payment_method(db, name="Nequi", is_cash=False, type="transfer")
        attempt = fx.make_payment_attempt(
            db, order, nequi, status="pendiente", receipt_file_url="https://example.invalid/a.jpg"
        )
        db.commit()

        result = checkout.reject_payment_attempt(
            db, attempt.id, "el monto no coincide", self._user()
        )
        self.assertEqual(result.status, "rechazado")
        self.assertEqual(result.rejection_reason, "el monto no coincide")

    def test_approve_o_reject_de_metodo_efectivo_falla_409(self):
        db, order = self._seed_order_recibida()
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.approve_payment_attempt(db, attempt.id, self._user())
        self.assertEqual(ctx.exception.status_code, 409)

    # ------------------------------------------------- confirm-cash (US3)

    def test_confirm_cash_calcula_cambio(self):
        """Acceptance Scenario 1 (US3): $18.000 de total, $20.000 recibidos →
        $2.000 de cambio."""
        db, order = self._seed_order_recibida(precio=PRECIO)
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()

        result = checkout.confirm_cash_payment_attempt(
            db, attempt.id, Decimal("20000"), self._user()
        )
        self.assertEqual(result.status, "confirmado")
        self.assertEqual(result.change_amount, Decimal("2000"))

    def test_confirm_cash_monto_exacto_cambio_cero(self):
        """Acceptance Scenario 2 (US3)."""
        db, order = self._seed_order_recibida(precio=PRECIO)
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()

        result = checkout.confirm_cash_payment_attempt(
            db, attempt.id, Decimal("18000"), self._user()
        )
        self.assertEqual(result.change_amount, Decimal("0"))

    def test_confirm_cash_monto_insuficiente_falla_422(self):
        """FR-010a, Acceptance Scenario 4 (US3): el sistema impide confirmar
        con un monto recibido menor al total."""
        db, order = self._seed_order_recibida(precio=PRECIO)
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.confirm_cash_payment_attempt(db, attempt.id, Decimal("15000"), self._user())
        self.assertEqual(ctx.exception.status_code, 422)
        # No quedó confirmado ni con cambio calculado.
        db.refresh(attempt)
        self.assertEqual(attempt.status, "pendiente")
        self.assertIsNone(attempt.change_amount)

    def test_confirm_cash_de_metodo_transferencia_falla_409(self):
        db, order = self._seed_order_recibida()
        nequi = fx.make_payment_method(db, name="Nequi", is_cash=False, type="transfer")
        attempt = fx.make_payment_attempt(db, order, nequi, status="pendiente")
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.confirm_cash_payment_attempt(db, attempt.id, Decimal("20000"), self._user())
        self.assertEqual(ctx.exception.status_code, 409)

    # ------------------------------------------------- FR-018/SC-007: doble resolución

    def test_segunda_resolucion_del_mismo_intento_falla_409(self):
        """FR-018/SC-007: ante dos resoluciones casi simultáneas del mismo
        intento, solo la primera surte efecto."""
        db, order = self._seed_order_recibida()
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()

        checkout.confirm_cash_payment_attempt(db, attempt.id, Decimal("20000"), self._user())
        with self.assertRaises(HTTPException) as ctx:
            checkout.confirm_cash_payment_attempt(db, attempt.id, Decimal("20000"), self._user())
        self.assertEqual(ctx.exception.status_code, 409)

    # ------------------------------------------------- confirm_order gate (US4)

    def test_confirm_order_409_con_intento_pendiente(self):
        """Acceptance Scenario 1 (US4)."""
        db, order = self._seed_order_recibida()
        nequi = fx.make_payment_method(db, name="Nequi", is_cash=False, type="transfer")
        fx.make_payment_attempt(db, order, nequi, status="pendiente")
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.confirm_order(db, order.id, self._user())
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("pago confirmado", str(ctx.exception.detail))

    def test_confirm_order_409_con_intento_rechazado(self):
        """Acceptance Scenario 2 (US4): un rechazo no habilita el paso a
        comanda, aunque el comensal ya lo haya visto."""
        db, order = self._seed_order_recibida()
        nequi = fx.make_payment_method(db, name="Nequi", is_cash=False, type="transfer")
        fx.make_payment_attempt(
            db, order, nequi, status="rechazado", rejection_reason="no coincide"
        )
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.confirm_order(db, order.id, self._user())
        self.assertEqual(ctx.exception.status_code, 409)

    def test_confirm_order_ok_con_intento_confirmado(self):
        """Acceptance Scenario 3 (US4): con un intento confirmado, el flujo
        normal de `confirm_order` sigue igual (descuenta inventario, pasa a
        'abierta')."""
        db, order = self._seed_order_recibida()
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        variant_id = order.items[0].product_variant_id
        from app.models.product_variant import ProductVariant
        variant = db.get(ProductVariant, variant_id)
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("2"))
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()
        checkout.confirm_cash_payment_attempt(db, attempt.id, Decimal("20000"), self._user())

        result = checkout.confirm_order(db, order.id, self._user())
        self.assertEqual(result.status, "abierta")

    def test_confirm_order_dos_veces_seguidas_la_segunda_no_tiene_efecto(self):
        """Acceptance Scenario 4 (US4): doble clic / dos cajeros casi
        simultáneos — solo la primera confirmación es válida."""
        db, order = self._seed_order_recibida()
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        variant_id = order.items[0].product_variant_id
        from app.models.product_variant import ProductVariant
        variant = db.get(ProductVariant, variant_id)
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("2"))
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()
        checkout.confirm_cash_payment_attempt(db, attempt.id, Decimal("20000"), self._user())

        checkout.confirm_order(db, order.id, self._user())
        with self.assertRaises(HTTPException) as ctx:
            checkout.confirm_order(db, order.id, self._user())
        self.assertEqual(ctx.exception.status_code, 409)

    # ------------------------------------------------- Historial (FR-016)

    def test_list_payment_attempts_incluye_rechazados_y_motivo(self):
        """Acceptance Scenario 3 (US5): el historial completo, con el motivo
        del rechazo, visible para el cajero."""
        db, order = self._seed_order_recibida()
        nequi = fx.make_payment_method(db, name="Nequi", is_cash=False, type="transfer")
        fx.make_payment_attempt(
            db, order, nequi, status="rechazado", rejection_reason="el monto no coincide",
        )
        fx.make_payment_attempt(db, order, nequi, status="pendiente")
        db.commit()

        attempts = checkout.list_payment_attempts(db, order.id)
        self.assertEqual(len(attempts), 2)
        rechazado = next(a for a in attempts if a.status == "rechazado")
        self.assertEqual(rechazado.rejection_reason, "el monto no coincide")

    # ---------------------------------- Orden creada por spec 025 (T020)

    def test_approve_y_reintento_tras_rechazo_sobre_orden_de_submit_cart(self):
        """spec 025-revision-pago-antes-envio, Acceptance Scenario 4 (US3):
        una orden creada por `cart.service.submit_cart(..., receipt_file_url=...)`
        (en vez de sembrada directamente con `fx.make_customer_order` +
        `fx.make_payment_attempt`, como el resto de este módulo) sigue
        pudiendo resolverse con `checkout.approve_payment_attempt`/
        `reject_payment_attempt`, sin cambios — incluido el reintento tras
        rechazo (`POST /cart/orders/{order_id}/payment-attempts`, spec 024
        Historia 5, sin cambios en esta spec)."""
        from app.api.v1.cart import service as cart_service

        db = fx.new_session()
        table = fx.make_dining_table(db)
        ts = fx.make_table_session(db, table=table)
        participant = fx.make_participant(db, table_session=ts)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("2"))
        cart = fx.make_cart(db, participant=participant)
        fx.make_cart_item(db, cart, variant)
        nequi = fx.make_payment_method(db, name="Nequi", is_cash=False, type="transfer")
        db.commit()

        order = cart_service.submit_cart(
            db, participant, nequi.id, receipt_file_url="https://example.invalid/a.jpg"
        )
        first_attempt_id = order.current_payment_attempt.id

        rejected = checkout.reject_payment_attempt(
            db, first_attempt_id, "el monto no coincide", self._user()
        )
        self.assertEqual(rejected.status, "rechazado")

        # Reintento tras rechazo: crea un intento nuevo sobre la misma orden
        # (spec 024, Historia 5) — endpoint separado, sin tocar en esta spec.
        second = cart_service.create_payment_attempt(db, participant.id, order.id, nequi.id)
        presign = cart_service.presign_receipt(
            db, "tenant_test", participant.id, second.id, "image/jpeg"
        )
        cart_service.attach_receipt(db, participant.id, second.id, presign.public_url)

        approved = checkout.approve_payment_attempt(db, second.id, self._user())
        self.assertEqual(approved.status, "confirmado")

        result = checkout.confirm_order(db, order.id, self._user())
        self.assertEqual(result.status, "abierta")


if __name__ == "__main__":
    unittest.main()
