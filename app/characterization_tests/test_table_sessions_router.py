"""Characterization tests de los 7 endpoints de
`app/api/v1/table_sessions/router.py` (specs/016-caracterizacion-table-sessions,
Historia 3).

Invoca las funciones de endpoint directamente como funciones Python (research.md
§1): ningún endpoint de `table_sessions/router.py` abre su propio contexto (a
diferencia de `cart/router.py`), así que basta con pasar dobles mínimos
(`SimpleNamespace`, `table_sessions_fixtures.make_tenant_double`/
`make_user_double`) donde el endpoint recibiría `Depends(get_tenant)`/
`Depends(get_current_user)` — `Depends(...)` nunca se resuelve al llamar la
función directamente.

Reutiliza el comportamiento de `service.py` ya congelado en
`test_table_sessions_split_blindaje.py` y `test_table_sessions_service.py` como
línea base (spec.md lo declara explícitamente: esta historia depende de las dos
anteriores, que ya cubren `service.py`).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_table_sessions_router -v
"""
from decimal import Decimal
import unittest

from fastapi import HTTPException

from app.characterization_tests import table_sessions_fixtures as fx
from app.api.v1.table_sessions import router as ts_router
from app.api.v1.table_sessions import service
from app.api.v1.table_sessions.schemas import (
    AssignmentsIn, CloseSessionIn, CloseSessionResponse, ItemAssignmentIn,
    ParticipantCreateIn, SessionBillResponse, TableSessionResponse,
)
from app.api.v1.sales.schemas import PaymentIn

PRECIO = Decimal("10000")


def _status_code_for(endpoint_name: str) -> int:
    """Status code declarado en el decorador de la ruta (`@router.get/post/...`),
    para las aserciones de código de respuesta que esta Historia congela sin
    pasar por `fastapi.testclient` (research.md §1: `Depends` nunca se resuelve
    al invocar la función Python directamente, así que el único código de
    estado observable "de verdad" al no usar ASGI es el que quedó registrado en
    la ruta). `APIRoute.status_code` es `None` cuando el decorador no lo fija
    explícito (`add_participant`/`remove_participant` sí lo hacen; el resto no)
    — FastAPI resuelve ese `None` a 200 en tiempo de respuesta real."""
    for route in ts_router.router.routes:
        if getattr(route, "endpoint", None) is not None and route.endpoint.__name__ == endpoint_name:
            return route.status_code or 200
    raise AssertionError(f"No existe ninguna ruta para el endpoint {endpoint_name!r}")


class TestTableSessionsRouter(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_billable_session(self):
        db = fx.new_session()
        table = fx.make_dining_table(db, status="ocupada")
        ts = fx.make_table_session(db, table=table)
        ana = fx.make_participant(db, table_session=ts, display_name="Ana", display_label="Ana")

        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        order = fx.make_customer_order(db, ts, status="abierta")
        item = fx.make_order_item(db, order, variant, participant_id=ana.id)

        register = fx.make_cash_register(db)
        shift = fx.make_cash_shift(db, register=register)
        method = fx.make_payment_method(db)

        db.commit()
        return dict(
            db=db, table=table, ts=ts, ana=ana, order=order, item=item,
            shift=shift, method=method,
            tenant=fx.make_tenant_double(id=1), user=fx.make_user_double(),
        )

    def _pago(self, method_id, amount=PRECIO):
        return PaymentIn(payment_method_id=method_id, amount=amount).model_dump(mode="json")

    # -------------------------------------------------- GET /table-sessions (T030)

    def test_list_sessions_endpoint(self):
        """CONGELA comportamiento actual: invocado directamente con
        `only_active=` explícito (nunca el default `Query(True, ...)` sin
        resolver, research.md §1), devuelve la lista de sesiones esperada."""
        db = fx.new_session()
        t1 = fx.make_dining_table(db)
        ts_active = fx.make_table_session(db, table=t1, status="active")
        t2 = fx.make_dining_table(db)
        fx.make_table_session(db, table=t2, status="closed")
        db.commit()
        user = fx.make_user_double()

        resp = ts_router.list_sessions(only_active=True, db=db, _=user)

        self.assertEqual({s.id for s in resp}, {ts_active.id})
        self.assertEqual(_status_code_for("list_sessions"), 200)

    # ----------------------------------------------- GET /table-sessions/{id} (T031)

    def test_get_session_endpoint_existente_y_404(self):
        """CONGELA comportamiento actual (spec.md Historia 3, escenario 1):
        sesión existente responde con `TableSessionResponse` incluyendo sus
        comensales; un id inexistente propaga la misma `HTTPException` 404 que
        `service.get_session` ya congeló."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        ts = fx.make_table_session(db, table=table)
        fx.make_participant(db, table_session=ts, display_name="Ana", display_label="Ana")
        db.commit()
        user = fx.make_user_double()

        got = ts_router.get_session(ts.id, db=db, _=user)
        self.assertEqual(got.id, ts.id)
        self.assertEqual(len(got.participants), 1)
        self.assertEqual(_status_code_for("get_session"), 200)

        from uuid import uuid4
        with self.assertRaises(HTTPException) as ctx:
            ts_router.get_session(uuid4(), db=db, _=user)
        self.assertEqual(ctx.exception.status_code, 404)

    # ------------------------------------- POST /participants (add_participant, T032)

    def test_add_participant_endpoint_nombre_vacio_422_y_valido_201(self):
        """CONGELA comportamiento actual (spec.md Historia 3, escenario 2): un
        `display_name` vacío o solo espacios responde 422 sin crear el comensal
        (`ParticipantCreateIn.min_length=1` lo bloquea antes de llegar al
        servicio para la cadena vacía; el servicio mismo rechaza la de solo
        espacios tras `.strip()`); un nombre válido responde 201 con
        `display_label` desambiguado."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        ts = fx.make_table_session(db, table=table)
        fx.make_participant(db, table_session=ts, display_name="Ana", display_label="Ana")
        db.commit()
        tenant = fx.make_tenant_double(id=1)
        user = fx.make_user_double()

        with self.assertRaises(Exception):
            ParticipantCreateIn(display_name="")

        with self.assertRaises(HTTPException) as ctx:
            ts_router.add_participant(
                ts.id, ParticipantCreateIn.model_construct(display_name="   "),
                db=db, tenant=tenant, _=user,
            )
        self.assertEqual(ctx.exception.status_code, 422)

        creado = ts_router.add_participant(
            ts.id, ParticipantCreateIn(display_name="Ana"), db=db, tenant=tenant, _=user,
        )
        self.assertEqual(creado.display_name, "Ana")
        self.assertEqual(creado.display_label, "Ana (2)")
        self.assertEqual(_status_code_for("add_participant"), 201)

    # --------------------------------------- DELETE /participants/{id} (T033)

    def test_remove_participant_endpoint_con_productos_asignados_409(self):
        """CONGELA comportamiento actual (spec.md Historia 3, escenario 3): un
        comensal con productos asignados responde 409 con el detalle de cuántos
        productos tiene asignados — congelando el contrato de error del
        endpoint (delegado íntegramente en `service.remove_participant`)."""
        s = self._seed_billable_session()

        with self.assertRaises(HTTPException) as ctx:
            ts_router.remove_participant(
                s["ts"].id, s["ana"].id, db=s["db"], tenant=s["tenant"], _=s["user"],
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("items", ctx.exception.detail)
        self.assertEqual(ctx.exception.detail["items"], 1)
        self.assertEqual(_status_code_for("remove_participant"), 204)

    # ----------------------------------------------- PUT /assignments (T034)

    def test_set_assignments_endpoint_responde_bill_recalculada(self):
        """CONGELA comportamiento actual (spec.md Historia 3, escenario 4): un
        lote de asignaciones válido responde con la `SessionBillResponse` ya
        recalculada, sin exigir una segunda llamada a `GET .../bill`."""
        s = self._seed_billable_session()
        beto = fx.make_participant(s["db"], table_session=s["ts"], display_name="Beto", display_label="Beto")
        s["db"].commit()

        body = AssignmentsIn(assignments=[
            ItemAssignmentIn(order_item_id=s["item"].id, participant_id=beto.id),
        ])

        resp = ts_router.set_assignments(
            s["ts"].id, body, db=s["db"], tenant=s["tenant"], _=s["user"],
        )

        self.assertIsInstance(resp, SessionBillResponse)
        por_comensal = {line.participant_id: line.subtotal for line in resp.split}
        self.assertEqual(por_comensal[beto.id], PRECIO)
        self.assertEqual(_status_code_for("set_assignments"), 200)

        item = s["db"].get(type(s["item"]), s["item"].id)
        self.assertEqual(item.participant_id, beto.id)

    # -------------------------------------------------------- GET /bill (T035)

    def test_session_bill_endpoint_delega_en_compute_bill(self):
        """CONGELA comportamiento actual: `GET .../bill` delega íntegramente en
        `service.compute_bill` y responde exactamente igual que la función ya
        congelada en `test_table_sessions_service.py`."""
        s = self._seed_billable_session()

        via_router = ts_router.session_bill(s["ts"].id, db=s["db"], _=s["user"])
        via_service = service.compute_bill(s["db"], s["ts"].id)

        self.assertEqual(via_router, via_service)
        self.assertEqual(_status_code_for("session_bill"), 200)

    # ------------------------------------------------------- POST /close (T036)

    def test_close_session_endpoint_unified_200(self):
        """CONGELA comportamiento actual (spec.md Historia 3, escenario 5): un
        `CloseSessionIn` válido para `billing_mode='unified'` responde con
        `CloseSessionResponse` (`table_session` ya `closed`, `sale_ids` con
        exactamente una venta)."""
        s = self._seed_billable_session()
        data = CloseSessionIn.model_validate({
            "cash_shift_id": str(s["shift"].id),
            "billing_mode": "unified",
            "payments": [self._pago(s["method"].id)],
        })

        resp = ts_router.close_session(
            s["ts"].id, data, db=s["db"], user=s["user"], tenant=s["tenant"],
        )

        self.assertIsInstance(resp, CloseSessionResponse)
        self.assertEqual(resp.table_session.status, "closed")
        self.assertEqual(len(resp.sale_ids), 1)
        self.assertEqual(_status_code_for("close_session"), 200)


if __name__ == "__main__":
    unittest.main()
