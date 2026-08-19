"""Characterization tests de las 9 funciones públicas de
`app/api/v1/table_sessions/service.py` (specs/016-caracterizacion-table-sessions,
Historia 2).

Cada test CONGELA el comportamiento actual, ejercitando `orders.checkout` y
`promotions.service` reales (sin mocks, FR-009) contra SQLite en memoria vía
`table_sessions_fixtures.py`. Incluye los cuatro casos de anomalía que esta
Historia cubre (FR-004, FR-006, FR-007, FR-008), citados explícitamente:

  - A-01 (camino A, correcto): `test_compute_bill_a01_...`
  - A-17 (R12): `test_add_remove_set_assignments_a17_r12_...`
  - A-29: `test_close_session_unified_a29_...`
  - A-38 (RN-MESA-13): `test_close_session_split_rn_mesa_13_a38_...`
  - A-38 (RN-MESA-24): `test_remove_participant_rn_mesa_24_a38_...`

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_table_sessions_service -v
"""
from decimal import Decimal
import unittest
from unittest import mock
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from app.characterization_tests import table_sessions_fixtures as fx
from app.api.v1.table_sessions import service
from app.api.v1.table_sessions.schemas import CloseSessionIn, ItemAssignmentIn
from app.api.v1.sales.schemas import PaymentIn
from app.models.dining_table import DiningTable
from app.models.order_item import OrderItem
from app.models.sale import Sale
from app.models.session_participant import SessionParticipant
from app.models.table_session import TableSession

PRECIO = Decimal("10000")


class TestTableSessionsService(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_bare_session(self, *, table_status: str = "ocupada"):
        """Mesa + `TableSession` activa, sin comensales ni pedidos aún."""
        db = fx.new_session()
        table = fx.make_dining_table(db, status=table_status)
        ts = fx.make_table_session(db, table=table)
        db.commit()
        return db, table, ts

    def _seed_billable_session(self):
        """Mesa + sesión activa + dos comensales (Ana, Beto) + infraestructura de
        caja + un pedido `abierta` con una línea de Ana, lista para cobrar."""
        db, table, ts = self._seed_bare_session()
        ana = fx.make_participant(db, table_session=ts, display_name="Ana", display_label="Ana")
        beto = fx.make_participant(db, table_session=ts, display_name="Beto", display_label="Beto")

        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)

        order = fx.make_customer_order(db, ts, status="abierta")
        item = fx.make_order_item(db, order, variant, participant_id=ana.id)

        register = fx.make_cash_register(db)
        shift = fx.make_cash_shift(db, register=register)
        method = fx.make_payment_method(db)
        cashier = fx.make_user_double()

        db.commit()
        return dict(
            db=db, table=table, ts=ts, ana=ana, beto=beto, category=category,
            product=product, variant=variant, order=order, item=item,
            shift=shift, method=method, cashier=cashier,
        )

    def _pago(self, method_id, amount=PRECIO):
        return PaymentIn(payment_method_id=method_id, amount=amount).model_dump(mode="json")

    def _ventas(self, db, ts_id) -> list[Sale]:
        db.flush()
        return list(db.execute(select(Sale).where(Sale.table_session_id == ts_id)).scalars())

    # ---------------------------------------------------------- get_session (T017)

    def test_get_session_existente_y_404(self):
        """CONGELA comportamiento actual: sesión existente devuelve el
        `TableSession` esperado (`service.py:38-59`); un id inexistente propaga
        `HTTPException` 404."""
        db, table, ts = self._seed_bare_session()

        got = service.get_session(db, ts.id)
        self.assertEqual(got.id, ts.id)
        self.assertEqual(got.dining_table_id, table.id)

        with self.assertRaises(HTTPException) as ctx:
            service.get_session(db, uuid4())
        self.assertEqual(ctx.exception.status_code, 404)

    # ------------------------------------------------- has_billable_orders (T018)

    def test_has_billable_orders_true_y_false(self):
        """CONGELA comportamiento actual (`service.py:64-71`): una sesión con un
        pedido `recibida`/`abierta` (no terminal) devuelve `True`; con solo
        pedidos `cancelada`/`pagada` devuelve `False`."""
        db, table, ts = self._seed_bare_session()
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)

        order_abierta = fx.make_customer_order(db, ts, status="abierta")
        fx.make_order_item(db, order_abierta, variant)
        db.commit()
        self.assertTrue(service.has_billable_orders(db, ts.id))

        order_abierta.status = "pagada"
        db.commit()
        order_cancelada = fx.make_customer_order(db, ts, status="cancelada")
        fx.make_order_item(db, order_cancelada, variant)
        db.commit()
        self.assertFalse(service.has_billable_orders(db, ts.id))

    # ----------------------------------------------- try_release_if_empty (T019)

    def test_try_release_if_empty_libera_y_no_libera(self):
        """CONGELA comportamiento actual (spec.md Historia 2, escenario 6): sin
        comensales activos y sin pedidos cobrables, libera la mesa y cierra la
        sesión; con un pedido todavía cobrable, no libera nada."""
        db, table, ts = self._seed_bare_session()
        participant = fx.make_participant(db, table_session=ts, status="closed")
        db.commit()

        released = service.try_release_if_empty(db, ts.id)
        self.assertTrue(released)
        db.refresh(ts)
        db.refresh(table)
        self.assertEqual(ts.status, "closed")
        self.assertEqual(table.status, "libre")

        # Segunda sesión: queda un pedido cobrable, no se libera.
        table2 = fx.make_dining_table(db, status="ocupada")
        ts2 = fx.make_table_session(db, table=table2)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        order = fx.make_customer_order(db, ts2, status="abierta")
        fx.make_order_item(db, order, variant)
        db.commit()

        released2 = service.try_release_if_empty(db, ts2.id)
        self.assertFalse(released2)
        db.refresh(ts2)
        self.assertEqual(ts2.status, "active")

    # -------------------------------------------------------- list_sessions (T020)

    def test_list_sessions_only_active_y_todas(self):
        """CONGELA comportamiento actual: `only_active=True` (explícito, nunca el
        default `Query` sin resolver, research.md §1) solo devuelve sesiones
        `active`; `only_active=False` incluye también sesiones `closed`."""
        db = fx.new_session()
        t1 = fx.make_dining_table(db)
        ts_active = fx.make_table_session(db, table=t1, status="active")
        t2 = fx.make_dining_table(db)
        ts_closed = fx.make_table_session(db, table=t2, status="closed")
        db.commit()

        solo_activas = service.list_sessions(db, only_active=True)
        self.assertEqual({s.id for s in solo_activas}, {ts_active.id})

        todas = service.list_sessions(db, only_active=False)
        self.assertEqual({s.id for s in todas}, {ts_active.id, ts_closed.id})

    # -------------------------------------------------------- compute_bill (T021)

    def test_compute_bill_a01_camino_base(self):
        """CONGELA comportamiento actual — A-01 (camino A, correcto,
        `service.py:139-181`): con pedidos en distintos estados repartidos entre
        dos comensales y una promoción + un combo activos, el `total` y el
        desglose por comensal excluyen los pedidos `cancelada`/`pagada`, y
        aplican el descuento de promoción/combo por comensal."""
        db, table, ts = self._seed_bare_session()
        ana = fx.make_participant(db, table_session=ts, display_name="Ana", display_label="Ana")
        beto = fx.make_participant(db, table_session=ts, display_name="Beto", display_label="Beto")

        # Ana: una línea con descuento percent del 10% (promoción automática).
        cat_a = fx.make_category(db)
        prod_a = fx.make_product(db, category=cat_a)
        variant_a = fx.make_variant(db, product=prod_a, price=Decimal("10000"))
        promo = fx.make_promotion(db, type="percent", value=Decimal("10"), status="active")
        fx.make_promotion_target(db, promo, category_id=cat_a.id)

        # Beto: un combo completo de dos variantes distintas.
        cat_b = fx.make_category(db)
        prod_b = fx.make_product(db, category=cat_b)
        variant_1 = fx.make_variant(db, product=prod_b, price=Decimal("6000"))
        variant_2 = fx.make_variant(db, product=prod_b, price=Decimal("7000"))
        combo = fx.make_promotion(db, type="combo", value=Decimal("11000"), status="active")
        fx.make_combo_item(db, combo, variant_1, quantity=1)
        fx.make_combo_item(db, combo, variant_2, quantity=1)

        order_abierta = fx.make_customer_order(db, ts, status="abierta")
        fx.make_order_item(db, order_abierta, variant_a, participant_id=ana.id, estado_cocina="listo")
        fx.make_order_item(
            db, order_abierta, variant_1, participant_id=beto.id,
            combo_id=combo.id, estado_cocina="en_preparacion",
        )
        fx.make_order_item(
            db, order_abierta, variant_2, participant_id=beto.id,
            combo_id=combo.id, estado_cocina="listo",
        )

        order_cancelada = fx.make_customer_order(db, ts, status="cancelada")
        fx.make_order_item(db, order_cancelada, variant_a, participant_id=ana.id)

        order_pagada = fx.make_customer_order(db, ts, status="pagada")
        fx.make_order_item(db, order_pagada, variant_a, participant_id=beto.id)

        db.commit()

        resp = service.compute_bill(db, ts.id)

        por_comensal = {line.participant_id: line.subtotal for line in resp.split}
        self.assertEqual(por_comensal[ana.id], Decimal("9000.00"))
        self.assertEqual(por_comensal[beto.id], Decimal("11000.00"))
        self.assertEqual(resp.total, Decimal("20000.00"))
        # Solo el pedido 'abierta' entra en la cuenta; cancelada/pagada, no.
        self.assertEqual(resp.order_ids, [order_abierta.id])

    def test_compute_bill_expone_items_y_descuento_por_comensal(self):
        """spec 026, FR-006: no es CONGELA — comportamiento nuevo. `compute_bill`
        ya calculaba las líneas y el descuento de cada comensal para llegar al
        `subtotal`; ahora esos mismos valores también se serializan en
        `SessionBillLine.items`/`discount`, sin cambiar ningún cálculo."""
        db, table, ts = self._seed_bare_session()
        ana = fx.make_participant(db, table_session=ts, display_name="Ana", display_label="Ana")

        cat = fx.make_category(db)
        prod = fx.make_product(db, category=cat)
        variant = fx.make_variant(db, product=prod, price=Decimal("10000"))
        promo = fx.make_promotion(db, type="percent", value=Decimal("10"), status="active")
        fx.make_promotion_target(db, promo, category_id=cat.id)

        order = fx.make_customer_order(db, ts, status="abierta")
        fx.make_order_item(db, order, variant, participant_id=ana.id, estado_cocina="listo", quantity=2)
        db.commit()

        resp = service.compute_bill(db, ts.id)

        line = next(l for l in resp.split if l.participant_id == ana.id)
        self.assertEqual(line.discount, Decimal("2000.00"))  # 10% de 20.000
        self.assertEqual(len(line.items), 1)
        self.assertEqual(line.items[0].quantity, Decimal("2"))
        self.assertEqual(line.items[0].unit_price, Decimal("10000.00"))
        self.assertEqual(line.items[0].line_total, Decimal("20000.00"))

    # -------------------------------------------------- close_session unified (T022)

    def test_close_session_unified_camino_feliz(self):
        """CONGELA comportamiento actual (`service.py:_close_unified`): una sola
        venta agrupa todo lo cobrable de la sesión, la sesión queda `closed` y la
        mesa se libera."""
        s = self._seed_billable_session()
        data = CloseSessionIn.model_validate({
            "cash_shift_id": str(s["shift"].id),
            "billing_mode": "unified",
            "payments": [self._pago(s["method"].id)],
        })

        resp = service.close_session(s["db"], s["ts"].id, data, s["cashier"])

        self.assertEqual(len(resp.sale_ids), 1)
        s["db"].refresh(s["ts"])
        s["db"].refresh(s["table"])
        self.assertEqual(s["ts"].status, "closed")
        self.assertEqual(s["table"].status, "libre")

    # ------------------------------------------------- A-17 (R12) — spy_load (T023)

    def test_add_remove_set_assignments_a17_r12_cargan_sin_lock(self):
        """CONGELA comportamiento actual — A-17 (R12, `service.py:38-55` y sus
        llamadas sin `lock=True` en `add_participant:335`,
        `remove_participant:370`, `set_assignments:403`): a diferencia de
        `close_session` (que carga con `_load(..., lock=True)`), estas tres
        funciones cargan la sesión sin bloqueo — reproducido sin concurrencia
        real, espiando `service._load` (spec.md Historia 2, escenario 2)."""
        s = self._seed_billable_session()
        db = s["db"]

        data = CloseSessionIn.model_validate({
            "cash_shift_id": str(s["shift"].id),
            "billing_mode": "unified",
            "payments": [self._pago(s["method"].id)],
        })

        with fx.spy_load() as spy:
            nuevo = service.add_participant(db, s["ts"].id, "Nuevo", tenant_id=None)
            service.remove_participant(db, s["ts"].id, nuevo.id, tenant_id=None)
            service.set_assignments(
                db, s["ts"].id,
                [ItemAssignmentIn(order_item_id=s["item"].id, participant_id=s["ana"].id)],
                tenant_id=None,
            )
            idx_antes_de_cerrar = len(spy.calls)
            service.close_session(db, s["ts"].id, data, s["cashier"])

        previas = spy.calls[:idx_antes_de_cerrar]
        self.assertTrue(previas)
        self.assertTrue(all(c.lock is False for c in previas))
        # `close_session` sí pide el lock, en su primera carga de la sesión.
        self.assertTrue(spy.calls[idx_antes_de_cerrar].lock)

    # --------------------------------------------------------------- A-29 (T024)

    def test_close_session_unified_a29_promotion_id_no_registra_combos_multiples(self):
        """CONGELA comportamiento actual — A-29 (parcial, `service.py:555-559`
        dentro de `_close_unified`): con dos combos distintos en la misma venta,
        `promotion_id` no registra ninguno, aunque el descuento monetario de
        ambos sí se suma correctamente al total."""
        db, table, ts = self._seed_bare_session()
        ana = fx.make_participant(db, table_session=ts)

        cat = fx.make_category(db)
        prod = fx.make_product(db, category=cat)
        v1a = fx.make_variant(db, product=prod, price=Decimal("6000"))
        v1b = fx.make_variant(db, product=prod, price=Decimal("7000"))
        combo1 = fx.make_promotion(db, type="combo", value=Decimal("11000"), status="active")
        fx.make_combo_item(db, combo1, v1a, quantity=1)
        fx.make_combo_item(db, combo1, v1b, quantity=1)

        v2a = fx.make_variant(db, product=prod, price=Decimal("4000"))
        v2b = fx.make_variant(db, product=prod, price=Decimal("5000"))
        combo2 = fx.make_promotion(db, type="combo", value=Decimal("8000"), status="active")
        fx.make_combo_item(db, combo2, v2a, quantity=1)
        fx.make_combo_item(db, combo2, v2b, quantity=1)

        order = fx.make_customer_order(db, ts, status="abierta")
        for variant, combo in ((v1a, combo1), (v1b, combo1), (v2a, combo2), (v2b, combo2)):
            fx.make_order_item(db, order, variant, participant_id=ana.id, combo_id=combo.id)

        register = fx.make_cash_register(db)
        shift = fx.make_cash_shift(db, register=register)
        method = fx.make_payment_method(db)
        cashier = fx.make_user_double()
        db.commit()

        data = CloseSessionIn.model_validate({
            "cash_shift_id": str(shift.id),
            "billing_mode": "unified",
            "payments": [self._pago(method.id, amount=Decimal("20000"))],
        })

        resp = service.close_session(db, ts.id, data, cashier)

        ventas = self._ventas(db, ts.id)
        self.assertEqual(len(ventas), 1)
        venta = ventas[0]
        # Ambos combos descuentan: (6000+7000-11000) + (4000+5000-8000) = 3000
        self.assertEqual(venta.discount, Decimal("3000.00"))
        self.assertIsNone(venta.promotion_id)

    # ---------------------------------------------------- RN-MESA-13 / A-38 (T025)

    def test_close_session_split_rn_mesa_13_a38_un_solo_comensal_sin_minimo(self):
        """CONGELA comportamiento actual — A-38 (RN-MESA-13, `service.py:578-671`):
        una mesa de un único comensal con consumo propio se puede cerrar en
        `billing_mode='split'` con un solo bloque para ese comensal, sin ninguna
        restricción de mínimo de comensales — equivalente en la práctica a un
        `unified` disfrazado."""
        db, table, ts = self._seed_bare_session()
        ana = fx.make_participant(db, table_session=ts, display_name="Ana", display_label="Ana")

        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        order = fx.make_customer_order(db, ts, status="abierta")
        fx.make_order_item(db, order, variant, participant_id=ana.id)

        register = fx.make_cash_register(db)
        shift = fx.make_cash_shift(db, register=register)
        method = fx.make_payment_method(db)
        cashier = fx.make_user_double()
        db.commit()

        data = CloseSessionIn.model_validate({
            "cash_shift_id": str(shift.id),
            "billing_mode": "split",
            "splits": [
                {"participant_id": str(ana.id), "payments": [self._pago(method.id)]},
            ],
        })

        resp = service.close_session(db, ts.id, data, cashier)

        self.assertEqual(len(resp.sale_ids), 1)
        db.refresh(ts)
        self.assertEqual(ts.status, "closed")

    # ---------------------------------------------------- RN-MESA-24 / A-38 (T026)

    def test_remove_participant_rn_mesa_24_a38_con_productos_asignados_409(self):
        """CONGELA comportamiento actual — A-38 (RN-MESA-24, `service.py:362-388`):
        `remove_participant` responde 409 y no quita a un comensal con al menos
        un producto asignado, sin distinguir si ese ítem está `anulado` o su
        pedido ya no es cobrable."""
        s = self._seed_billable_session()
        db, ts, ana, item = s["db"], s["ts"], s["ana"], s["item"]

        with self.assertRaises(HTTPException) as ctx:
            service.remove_participant(db, ts.id, ana.id, tenant_id=None)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIsNotNone(db.get(SessionParticipant, ana.id))

        # Mismo resultado con el único ítem asignado ya anulado y su pedido
        # pagado (no cobrable): la consulta de `remove_participant` no filtra
        # por `estado_cocina` ni por `CustomerOrder.status`.
        item.estado_cocina = "anulado"
        s["order"].status = "pagada"
        db.commit()

        with self.assertRaises(HTTPException) as ctx2:
            service.remove_participant(db, ts.id, ana.id, tenant_id=None)
        self.assertEqual(ctx2.exception.status_code, 409)
        self.assertIsNotNone(db.get(SessionParticipant, ana.id))

    # ------------------------------------------- bill_changed tras commit (T027)

    def test_bill_changed_se_publica_una_vez_tras_commit(self):
        """CONGELA comportamiento actual (FR-010a, spec.md Historia 2, escenario
        7): `add_participant`, `remove_participant` y `set_assignments`, al
        tener éxito, invocan `app.core.events.bill_changed` exactamente una vez,
        con el `tenant_id`/`table_session_id` correctos, después de que la
        transacción del `service` ya hizo commit (`db` refleja el nuevo estado
        dentro del propio callback interceptado)."""
        s = self._seed_billable_session()
        db, ts = s["db"], s["ts"]
        seen: dict = {}

        def _spy_add(tenant_id, *, table_session_id):
            seen["count_after_add"] = db.execute(
                select(func.count(SessionParticipant.id))
                .where(SessionParticipant.table_session_id == table_session_id)
            ).scalar_one()

        with mock.patch("app.core.events.bill_changed", side_effect=_spy_add) as spy:
            nuevo = service.add_participant(db, ts.id, "Carla", tenant_id=42)
        spy.assert_called_once_with(42, table_session_id=ts.id)
        self.assertEqual(seen["count_after_add"], 3)  # Ana, Beto, Carla

        def _spy_remove(tenant_id, *, table_session_id):
            seen["removed_visible"] = db.get(SessionParticipant, nuevo.id)

        with mock.patch("app.core.events.bill_changed", side_effect=_spy_remove) as spy2:
            service.remove_participant(db, ts.id, nuevo.id, tenant_id=42)
        spy2.assert_called_once_with(42, table_session_id=ts.id)
        self.assertIsNone(seen["removed_visible"])

        def _spy_set(tenant_id, *, table_session_id):
            seen["item_participant_after_commit"] = db.get(OrderItem, s["item"].id).participant_id

        with mock.patch("app.core.events.bill_changed", side_effect=_spy_set) as spy3:
            service.set_assignments(
                db, ts.id,
                [ItemAssignmentIn(order_item_id=s["item"].id, participant_id=s["beto"].id)],
                tenant_id=42,
            )
        spy3.assert_called_once_with(42, table_session_id=ts.id)
        self.assertEqual(seen["item_participant_after_commit"], s["beto"].id)

    # -------------------------------------------------- add_participant (T028)

    def test_add_participant_camino_feliz_desambigua_nombre(self):
        """CONGELA comportamiento actual: `add_participant` desambigua
        `display_name` vía `_unique_label`/`unique_display_label` de
        `cart.service` (ya congelado por `specs/015-caracterizacion-cart/`):
        "Ana" repetido en la misma sesión queda "Ana (2)"."""
        db, table, ts = self._seed_bare_session()
        fx.make_participant(db, table_session=ts, display_name="Ana", display_label="Ana")
        db.commit()

        nuevo = service.add_participant(db, ts.id, "Ana", tenant_id=None)

        self.assertEqual(nuevo.display_name, "Ana")
        self.assertEqual(nuevo.display_label, "Ana (2)")
        self.assertEqual(nuevo.status, "open")
        self.assertEqual(nuevo.table_session_id, ts.id)


if __name__ == "__main__":
    unittest.main()
