"""Tests de la nueva funcionalidad — spec 024-pagos-ordenes-mesa, User Stories
2 (revisión de comprobante), 3 (confirmación de efectivo) y 4 (gate de
comanda): FR-009/FR-010/FR-010a/FR-013/FR-014/FR-017/FR-018.

No son characterization tests: la revisión de comprobantes, la confirmación de
efectivo por intento y el gate de `confirm_order` son comportamiento nuevo
(research.md spec 024, Decisiones 5 y 9) — se verifican contra
`spec.md`/`contracts/cashier-payment-review.md`/`contracts/order-confirm-gate.md`,
no contra un comportamiento heredado.

Extendido por spec 026-mejoras-ux-comanda (FR-001/FR-002, research.md
Decisión 1): `confirm_cash_payment_attempt`/`approve_payment_attempt` ahora
disparan `_confirm_order_impl` (antes `confirm_order`, llamado manualmente
aparte) dentro de su propia transacción — confirmar el pago y enviar el
pedido a cocina ocurren en una sola llamada. Por eso `_seed_order_recibida`
siembra receta+inventario por defecto: cualquier confirmación exitosa ahora
también descuenta inventario, no solo cambia el estado del intento de pago.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_orders_payment_gate -v
"""
from decimal import Decimal
import unittest

from fastapi import HTTPException
from sqlalchemy import select

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.orders import checkout
from app.models.inventory_movement import InventoryMovement
from app.models.sale import Sale

PRECIO = Decimal("18000")


class TestOrdersPaymentGate(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_order_recibida(self, *, precio=PRECIO, stock=Decimal("1000"), autoflush=True):
        """spec 026: siempre siembra receta+inventario con stock amplio, para
        que confirmar el pago (que ahora también descuenta inventario) no
        falle por falta de receta en los tests que no están probando
        justamente eso (ver `_seed_order_recibida_sin_receta` más abajo).

        `autoflush=False` reproduce la sesión real de producción (spec 028):
        ver `test_confirm_cash_y_approve_funcionan_con_autoflush_false`."""
        db = fx.new_session(autoflush=autoflush)
        table = fx.make_dining_table(db)
        ts = fx.make_table_session(db, table=table)
        participant = fx.make_participant(db, table_session=ts)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=precio)
        insumo = fx.make_inventory_item(db, current_stock=stock)
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("1"))
        order = fx.make_customer_order(db, ts, participant=participant, status="recibida")
        fx.make_order_item(db, order, variant, estado_cocina="pendiente")
        db.commit()
        return db, order

    def _user(self):
        return fx.make_user_double()

    def _shift(self, db):
        """Spec 028: aprobar/confirmar ya factura en la misma llamada, así
        que necesita un turno de caja abierto (mismo requisito que
        `pay_order`/`checkout_and_send`)."""
        return fx.make_cash_shift(db)

    # ------------------------------------------------- approve/reject (US2)

    def test_approve_confirma_intento_de_transferencia(self):
        """Acceptance Scenario 4 (US2)."""
        db, order = self._seed_order_recibida()
        nequi = fx.make_payment_method(db, name="Nequi", is_cash=False, type="transfer")
        attempt = fx.make_payment_attempt(
            db, order, nequi, status="pendiente", receipt_file_url="https://example.invalid/a.jpg"
        )
        db.commit()

        result = checkout.approve_payment_attempt(db, attempt.id, self._shift(db).id, self._user())
        self.assertEqual(result.status, "confirmado")
        self.assertIsNotNone(result.resolved_at)
        # spec 028: aprobar ya genera la venta/factura en la misma llamada.
        db.refresh(order)
        sale = db.execute(select(Sale).where(Sale.customer_order_id == order.id)).scalar_one()
        self.assertEqual(sale.status, "paid")
        self.assertIsNotNone(sale.invoice)

    def test_approve_409_sin_comprobante_todavia(self):
        db, order = self._seed_order_recibida()
        nequi = fx.make_payment_method(db, name="Nequi", is_cash=False, type="transfer")
        attempt = fx.make_payment_attempt(db, order, nequi, status="pendiente")
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.approve_payment_attempt(db, attempt.id, self._shift(db).id, self._user())
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
            checkout.approve_payment_attempt(db, attempt.id, self._shift(db).id, self._user())
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
            db, attempt.id, Decimal("20000"), self._shift(db).id, self._user()
        )
        self.assertEqual(result.status, "confirmado")
        self.assertEqual(result.change_amount, Decimal("2000"))
        # spec 028: confirmar ya genera la venta/factura en la misma llamada
        # — mismo cambio calculado en ambos lados (Sale.change_given y
        # attempt.change_amount coinciden por construcción).
        db.refresh(order)
        sale = db.execute(select(Sale).where(Sale.customer_order_id == order.id)).scalar_one()
        self.assertEqual(sale.status, "paid")
        self.assertEqual(sale.change_given, Decimal("2000"))
        self.assertIsNotNone(sale.invoice)

    def test_confirm_cash_monto_exacto_cambio_cero(self):
        """Acceptance Scenario 2 (US3)."""
        db, order = self._seed_order_recibida(precio=PRECIO)
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()

        result = checkout.confirm_cash_payment_attempt(
            db, attempt.id, Decimal("18000"), self._shift(db).id, self._user()
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
            checkout.confirm_cash_payment_attempt(
                db, attempt.id, Decimal("15000"), self._shift(db).id, self._user()
            )
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
            checkout.confirm_cash_payment_attempt(
            db, attempt.id, Decimal("20000"), self._shift(db).id, self._user()
        )
        self.assertEqual(ctx.exception.status_code, 409)

    # ------------------------------------------------- FR-018/SC-007: doble resolución

    def test_segunda_resolucion_del_mismo_intento_falla_409(self):
        """FR-018/SC-007: ante dos resoluciones casi simultáneas del mismo
        intento, solo la primera surte efecto. Reforzado por spec 026,
        FR-001: tampoco duplica el envío a cocina (un solo movimiento de
        inventario)."""
        db, order = self._seed_order_recibida()
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()

        checkout.confirm_cash_payment_attempt(
            db, attempt.id, Decimal("20000"), self._shift(db).id, self._user()
        )
        with self.assertRaises(HTTPException) as ctx:
            checkout.confirm_cash_payment_attempt(
            db, attempt.id, Decimal("20000"), self._shift(db).id, self._user()
        )
        self.assertEqual(ctx.exception.status_code, 409)

        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 1)

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
        """Acceptance Scenario 3 (US4), actualizado por spec 026 FR-001: con
        un intento de pago recién confirmado en efectivo, la orden queda
        'abierta' (inventario descontado) en la MISMA llamada a
        `confirm_cash_payment_attempt` — ya no hace falta una segunda llamada
        manual a `confirm_order` (research.md spec 026, Decisión 1).
        `confirm_order` sigue expuesto como vía de recuperación (Decisión 2),
        pero sobre una orden que ya está 'abierta' no tiene nada que hacer."""
        db, order = self._seed_order_recibida()
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()

        result = checkout.confirm_cash_payment_attempt(
            db, attempt.id, Decimal("20000"), self._shift(db).id, self._user()
        )
        self.assertEqual(result.status, "confirmado")
        db.refresh(order)
        self.assertEqual(order.status, "abierta")

        with self.assertRaises(HTTPException) as ctx:
            checkout.confirm_order(db, order.id, self._user())
        self.assertEqual(ctx.exception.status_code, 409)

    def test_confirm_order_dos_veces_seguidas_la_segunda_no_tiene_efecto(self):
        """Acceptance Scenario 4 (US4): `confirm_order`, llamado directamente
        (vía de recuperación, spec 026 research.md Decisión 2) sobre una
        orden con un intento ya confirmado, sigue siendo idempotente — solo
        la primera llamada tiene efecto."""
        db, order = self._seed_order_recibida()
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        fx.make_payment_attempt(db, order, efectivo, status="confirmado")
        db.commit()

        result = checkout.confirm_order(db, order.id, self._user())
        self.assertEqual(result.status, "abierta")

        with self.assertRaises(HTTPException) as ctx:
            checkout.confirm_order(db, order.id, self._user())
        self.assertEqual(ctx.exception.status_code, 409)

    # ------------------------------------------------- Fusión pago→cocina (spec 026, FR-001/FR-002)

    def test_confirm_cash_envia_a_cocina_en_la_misma_llamada(self):
        """spec 026, FR-001 (US1, escenario 1): confirmar el efectivo
        descuenta inventario y deja la orden 'abierta' en la misma llamada,
        sin ninguna acción manual adicional."""
        db, order = self._seed_order_recibida()
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()

        checkout.confirm_cash_payment_attempt(
            db, attempt.id, Decimal("20000"), self._shift(db).id, self._user()
        )

        db.refresh(order)
        self.assertEqual(order.status, "abierta")
        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 1)

    def test_approve_envia_a_cocina_en_la_misma_llamada(self):
        """spec 026, FR-001 (US1, escenario 2): aprobar un comprobante de
        transferencia descuenta inventario y deja la orden 'abierta' en la
        misma llamada."""
        db, order = self._seed_order_recibida()
        nequi = fx.make_payment_method(db, name="Nequi", is_cash=False, type="transfer")
        attempt = fx.make_payment_attempt(
            db, order, nequi, status="pendiente", receipt_file_url="https://example.invalid/a.jpg"
        )
        db.commit()

        checkout.approve_payment_attempt(db, attempt.id, self._shift(db).id, self._user())

        db.refresh(order)
        self.assertEqual(order.status, "abierta")
        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 1)

    def test_confirm_cash_y_approve_funcionan_con_autoflush_false(self):
        """Regresión (spec 028): la sesión real de producción
        (`app/core/db.py::with_db`) usa `autoflush=False`, a diferencia del
        default de esta suite (`fx.new_session()`, `autoflush=True`) — que
        por eso nunca detectó que `confirm_cash_payment_attempt` y
        `approve_payment_attempt` mutaban `attempt.status = "confirmado"` y
        llamaban a `_confirm_order_impl` sin un `db.flush()` de por medio: su
        chequeo `has_confirmed_payment` es una `SELECT` fresca que, sin
        flush, no ve el `UPDATE` todavía pendiente y siempre rechazaba con
        409 "La orden no tiene un pago confirmado" — en producción, **cada
        intento de confirmar un pago fallaba**. Este test reproduce la
        configuración real de la sesión para que esa regresión no pueda
        volver a colarse sin que la suite la detecte."""
        db, order = self._seed_order_recibida(autoflush=False)
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()

        result = checkout.confirm_cash_payment_attempt(
            db, attempt.id, Decimal("20000"), self._shift(db).id, self._user()
        )
        self.assertEqual(result.status, "confirmado")
        self.assertEqual(result.change_amount, Decimal("2000"))
        db.refresh(order)
        self.assertEqual(order.status, "abierta")

        db2, order2 = self._seed_order_recibida(autoflush=False)
        nequi = fx.make_payment_method(db2, name="Nequi", is_cash=False, type="transfer")
        attempt2 = fx.make_payment_attempt(
            db2, order2, nequi, status="pendiente", receipt_file_url="https://example.invalid/a.jpg"
        )
        db2.commit()

        result2 = checkout.approve_payment_attempt(db2, attempt2.id, self._shift(db2).id, self._user())
        self.assertEqual(result2.status, "confirmado")
        db2.refresh(order2)
        self.assertEqual(order2.status, "abierta")

    def test_confirm_cash_stock_insuficiente_no_confirma_el_pago(self):
        """spec 026, FR-002 (US1, escenario 3): si el descuento automático de
        inventario falla por falta de stock, el intento de pago NO queda
        confirmado y la orden NO avanza — ninguna de las dos cosas ocurre a
        medias (research.md Decisión 1: un solo `commit`/`rollback` cubre
        ambos cambios)."""
        db, order = self._seed_order_recibida(stock=Decimal("0"))
        efectivo = fx.make_payment_method(db, name="Efectivo", is_cash=True)
        attempt = fx.make_payment_attempt(db, order, efectivo, status="pendiente")
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.confirm_cash_payment_attempt(
            db, attempt.id, Decimal("20000"), self._shift(db).id, self._user()
        )
        self.assertEqual(ctx.exception.status_code, 400)

        db.refresh(attempt)
        db.refresh(order)
        self.assertEqual(attempt.status, "pendiente")
        self.assertIsNone(attempt.change_amount)
        self.assertEqual(order.status, "recibida")
        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 0)

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

        approved = checkout.approve_payment_attempt(db, second.id, self._shift(db).id, self._user())
        self.assertEqual(approved.status, "confirmado")

        # spec 026, FR-001: aprobar ya deja la orden 'abierta' en la misma
        # llamada — no hace falta una segunda llamada manual a confirm_order.
        db.refresh(order)
        self.assertEqual(order.status, "abierta")


if __name__ == "__main__":
    unittest.main()
