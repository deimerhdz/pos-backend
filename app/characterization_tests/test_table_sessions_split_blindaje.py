"""Characterization tests del blindaje de `close_session(billing_mode='split')`
(specs/016-caracterizacion-table-sessions, Historia 1).

CONGELA comportamiento actual: cita explícitamente **A-15 [PROTEGIDA]**
(`service.py:590-632`, dentro de `_close_split`) — cuatro huecos de seguridad ya
reales una vez y cerrados el 2026-08-04 (commit `42b5dec3`):

  1. comensal repetido en el mismo split cobrado dos veces;
  2. importes de raíz (`discount`/`tax`/`tip`/`payments`) ignorados en silencio
     cuando `billing_mode='split'`;
  3. el bloque sin comensal asignado saliendo sin nombre en la venta/factura;
  4. la cobertura exacta comensal-con-consumo ↔ comensal-en-el-split (ni falta
     ni sobra).

Es el invariante de mayor prioridad de toda esta spec (FR-005, SC-002): recibe,
de las cinco anomalías cubiertas, el mayor número de casos — uno por cada hueco
de seguridad cerrado, más el camino feliz. Migra y formaliza los casos de
`app/scripts/test_split_blindaje.py` (legado, A-27) al formato `unittest` de
`app/characterization_tests/`.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_table_sessions_split_blindaje -v
"""
from decimal import Decimal
import unittest

from fastapi import HTTPException

from app.characterization_tests import table_sessions_fixtures as fx
from app.api.v1.table_sessions import service
from app.api.v1.table_sessions.schemas import CloseSessionIn
from app.models.sale import Sale
from app.models.table_session import TableSession

PRECIO = Decimal("10000")


class TestTableSessionsSplitBlindaje(unittest.TestCase):
    """Cada test siembra su propia mesa con sesión activa, dos comensales (Ana y
    Beto) y un pedido de tres líneas: una de cada comensal y una sin asignar (lo
    que tecleó el mesero) — el mismo escenario mínimo que
    `app/scripts/test_split_blindaje.py`."""

    # ------------------------------------------------------------- Helpers

    def _seed(self):
        db = fx.new_session()
        register = fx.make_cash_register(db)
        shift = fx.make_cash_shift(db, register=register)
        method = fx.make_payment_method(db)
        cashier = fx.make_user_double()

        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)

        table = fx.make_dining_table(db)
        ts = fx.make_table_session(db, table=table)
        ana = fx.make_participant(db, table_session=ts, display_name="Ana", display_label="Ana")
        beto = fx.make_participant(db, table_session=ts, display_name="Beto", display_label="Beto")

        order = fx.make_customer_order(db, ts)
        for participant_id in (ana.id, beto.id, None):
            fx.make_order_item(db, order, variant, participant_id=participant_id)

        # `close_session` hace `db.rollback()` en sus caminos de error (para no
        # dejar a medias un cierre parcial) — sin comitear la siembra aquí, ese
        # rollback también se llevaría por delante la mesa/sesión/comensales que
        # este helper acaba de crear, y la siguiente llamada (mismo test o el
        # siguiente subTest) encontraría un 404 en vez del 422 esperado.
        db.commit()

        return dict(
            db=db, shift=shift, method=method, cashier=cashier,
            table=table, ts=ts, ana=ana, beto=beto, order=order,
        )

    def _pago(self, method_id, amount=PRECIO):
        from app.api.v1.sales.schemas import PaymentIn
        return PaymentIn(payment_method_id=method_id, amount=amount)

    def _ventas(self, db, ts_id) -> list[Sale]:
        from sqlalchemy import select
        db.flush()
        return list(db.execute(select(Sale).where(Sale.table_session_id == ts_id)).scalars())

    # ------------------------------------------ A-15: comensal repetido (T011)

    def test_close_session_split_a15_comensal_repetido_422_sin_ventas(self):
        """CONGELA comportamiento actual (A-15 [PROTEGIDA], hueco 1/4): dos
        bloques de `splits` con el mismo `participant_id` responden 422 citando
        los comensales repetidos, sin crear ninguna venta — un comensal no puede
        pagar dos veces por el mismo consumo."""
        s = self._seed()
        data = CloseSessionIn.model_validate({
            "cash_shift_id": str(s["shift"].id),
            "billing_mode": "split",
            "splits": [
                {"participant_id": str(s["ana"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
                {"participant_id": str(s["ana"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
                {"participant_id": str(s["beto"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
                {"participant_id": None, "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
            ],
        })

        with self.assertRaises(HTTPException) as ctx:
            service.close_session(s["db"], s["ts"].id, data, s["cashier"])
        self.assertEqual(ctx.exception.status_code, 422)
        detalle = str(ctx.exception.detail).lower()
        self.assertTrue("repetid" in detalle or "una sola vez" in detalle)

        self.assertEqual(self._ventas(s["db"], s["ts"].id), [])
        self.assertEqual(s["db"].get(TableSession, s["ts"].id).status, "active")

    # --------------------------------------- A-15: importes en la raíz (T012)

    def test_close_session_split_a15_importes_en_raiz_422(self):
        """CONGELA comportamiento actual (A-15 [PROTEGIDA], hueco 2/4): con
        `billing_mode='split'`, cualquiera de `discount`/`tax`/`tip`/`payments`
        puesto en la raíz del payload (no dentro de un bloque de `splits`)
        responde 422 en vez de aceptarlo y perderlo en silencio."""
        s = self._seed()
        splits_validos = [
            {"participant_id": str(s["ana"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
            {"participant_id": str(s["beto"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
            {"participant_id": None, "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
        ]

        casos_raiz = [
            {"tip": "5.00"},
            {"tax": "5.00"},
            {"discount": "1.00"},
            {"payments": [self._pago(s["method"].id).model_dump(mode="json")]},
        ]
        for extra in casos_raiz:
            with self.subTest(extra=extra):
                payload = {
                    "cash_shift_id": str(s["shift"].id),
                    "billing_mode": "split",
                    "splits": splits_validos,
                    **extra,
                }
                data = CloseSessionIn.model_validate(payload)
                with self.assertRaises(HTTPException) as ctx:
                    service.close_session(s["db"], s["ts"].id, data, s["cashier"])
                self.assertEqual(ctx.exception.status_code, 422)

        self.assertEqual(self._ventas(s["db"], s["ts"].id), [])

    # ------------------------------------- A-15: bloque sin comensal (T013)

    def test_close_session_split_a15_bloque_sin_comensal_usa_nombre_de_mesa(self):
        """CONGELA comportamiento actual (A-15 [PROTEGIDA], hueco 3/4): el bloque
        de `splits` sin `participant_id` (lo que tecleó el mesero) produce una
        venta con `customer_name == "Mesa {number}"`, nunca un nombre vacío."""
        s = self._seed()
        data = CloseSessionIn.model_validate({
            "cash_shift_id": str(s["shift"].id),
            "billing_mode": "split",
            "splits": [
                {"participant_id": str(s["ana"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
                {"participant_id": str(s["beto"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
                {"participant_id": None, "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
            ],
        })

        service.close_session(s["db"], s["ts"].id, data, s["cashier"])

        ventas = self._ventas(s["db"], s["ts"].id)
        sin_comensal = [v for v in ventas if v.participant_id is None]
        self.assertEqual(len(sin_comensal), 1)
        self.assertEqual(sin_comensal[0].customer_name, f"Mesa {s['table'].number}")
        self.assertTrue(all((v.customer_name or "") for v in ventas))

    # ------------------------------------- A-15: cobertura exacta (T014)

    def test_close_session_split_a15_cobertura_incompleta_422(self):
        """CONGELA comportamiento actual (A-15 [PROTEGIDA], hueco 4/4): si el
        `data.splits` recibido no cubre exactamente el conjunto de comensales con
        consumo real (falta uno, o sobra uno sin consumo), `close_session`
        responde 422 citando específicamente los comensales que faltan o sobran,
        sin cobrar nada."""
        s = self._seed()

        # Falta Beto (tiene consumo, pero no aparece en los splits).
        data_falta = CloseSessionIn.model_validate({
            "cash_shift_id": str(s["shift"].id),
            "billing_mode": "split",
            "splits": [
                {"participant_id": str(s["ana"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
                {"participant_id": None, "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
            ],
        })
        with self.assertRaises(HTTPException) as ctx:
            service.close_session(s["db"], s["ts"].id, data_falta, s["cashier"])
        self.assertEqual(ctx.exception.status_code, 422)
        detalle = str(ctx.exception.detail).lower()
        self.assertIn("falta", detalle)
        self.assertEqual(self._ventas(s["db"], s["ts"].id), [])

        # Sobra: un tercer comensal sin ningún consumo en la sesión.
        extra = fx.make_participant(s["db"], table_session=s["ts"], display_name="Caro")
        data_sobra = CloseSessionIn.model_validate({
            "cash_shift_id": str(s["shift"].id),
            "billing_mode": "split",
            "splits": [
                {"participant_id": str(s["ana"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
                {"participant_id": str(s["beto"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
                {"participant_id": None, "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
                {"participant_id": str(extra.id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
            ],
        })
        with self.assertRaises(HTTPException) as ctx2:
            service.close_session(s["db"], s["ts"].id, data_sobra, s["cashier"])
        self.assertEqual(ctx2.exception.status_code, 422)
        detalle2 = str(ctx2.exception.detail).lower()
        self.assertIn("sin consumo", detalle2)
        self.assertEqual(self._ventas(s["db"], s["ts"].id), [])

    # ------------------------------------------------- A-15: camino feliz (T015)

    def test_close_session_split_a15_camino_feliz_una_venta_por_comensal(self):
        """CONGELA comportamiento actual: un split válido que cubre exactamente a
        los comensales con consumo, sin repetidos y sin importes en la raíz,
        genera una venta por comensal (cada una con su propio `customer_name`) y
        deja la sesión `closed`."""
        s = self._seed()
        data = CloseSessionIn.model_validate({
            "cash_shift_id": str(s["shift"].id),
            "billing_mode": "split",
            "splits": [
                {"participant_id": str(s["ana"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
                {"participant_id": str(s["beto"].id), "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
                {"participant_id": None, "payments": [self._pago(s["method"].id).model_dump(mode="json")]},
            ],
        })

        resp = service.close_session(s["db"], s["ts"].id, data, s["cashier"])

        self.assertEqual(len(resp.sale_ids), 3)
        ventas = self._ventas(s["db"], s["ts"].id)
        nombres = sorted(v.customer_name for v in ventas)
        self.assertEqual(nombres, sorted(["Ana", "Beto", f"Mesa {s['table'].number}"]))
        self.assertEqual(sorted(v.subtotal for v in ventas), [PRECIO] * 3)

        s["db"].refresh(s["ts"])
        self.assertEqual(s["ts"].status, "closed")


if __name__ == "__main__":
    unittest.main()
