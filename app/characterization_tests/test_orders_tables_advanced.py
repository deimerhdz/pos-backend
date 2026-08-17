"""CONGELA comportamiento actual: las 4 funciones públicas de
`app/api/v1/orders/tables_advanced.py` (specs/017-caracterizacion-orders,
Historia 4) — mover una orden de mesa, fusionar mesas por grupo, y la cuenta
agrupada de un grupo fusionado.

Documenta los tres hallazgos de **A-26**:
  - RN-ORD-58: `move_order` exige la mesa destino completamente libre de
    órdenes activas, más estricto que el modelo general de varias órdenes
    por mesa.
  - RN-ORD-60: el manejador `except IntegrityError` de `move_order` está
    huérfano — el índice único que lo disparaba ya no existe en el modelo;
    se fuerza artificialmente con `orders_fixtures.force_flush_integrity_error`
    (Clarifications, sesión 2026-08-17).
  - RN-ORD-63: `merge_orders`, ante grupos preexistentes en colisión, resuelve
    el ganador con un `SELECT` sin `ORDER BY` — no determinista.

Y el camino C de **A-01** (`group_bill`, sin filtro de status ni descuentos,
en uso real para mesas fusionadas).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_orders_tables_advanced -v
"""
from decimal import Decimal
import unittest

from fastapi import HTTPException

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.orders import tables_advanced

PRECIO = Decimal("10000")


class TestTablesAdvanced(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_table_con_orden_activa(self, db=None, *, status="abierta"):
        if db is None:
            db = fx.new_session()
        table = fx.make_dining_table(db, status="ocupada")
        ts = fx.make_table_session(db, table=table)
        order = fx.make_customer_order(db, ts, status=status)
        db.commit()
        return db, table, ts, order

    # -------------------------------------------------------- set_table_status (T042)

    def test_set_table_status_409_con_ordenes_activas_y_ok_sin_ellas(self):
        """CONGELA comportamiento actual (`tables_advanced.py:30-43`,
        spec.md Historia 4, escenario 5): mesa con al menos una orden activa
        → `new_status='libre'`/`'reservada'` responde 409; sin órdenes
        activas, el cambio se acepta."""
        db, table, ts, order = self._seed_table_con_orden_activa(status="abierta")

        with self.assertRaises(HTTPException) as ctx:
            tables_advanced.set_table_status(db, table.id, "libre")
        self.assertEqual(ctx.exception.status_code, 409)

        order.status = "pagada"
        db.commit()

        result = tables_advanced.set_table_status(db, table.id, "libre")
        self.assertEqual(result.status, "libre")

    # ---------------------------------------------------------- move_order (T043)

    def test_move_order_409_si_mesa_destino_tiene_orden_activa_rn_ord_58(self):
        """CONGELA comportamiento actual — RN-ORD-58/A-26
        (`tables_advanced.move_order:45-73`, spec.md Historia 4, escenario
        1): mesa destino con una orden activa ya presente → `move_order`
        responde 409, más estricto que el modelo general de "varias órdenes
        por mesa"."""
        db, origen, ts_origen, order = self._seed_table_con_orden_activa(status="abierta")
        _, destino, _, _ = self._seed_table_con_orden_activa(db, status="abierta")

        with self.assertRaises(HTTPException) as ctx:
            tables_advanced.move_order(db, order.id, destino.id)
        self.assertEqual(ctx.exception.status_code, 409)

    # ----------------------------------- move_order IntegrityError forzado (T044)

    def test_move_order_integrity_error_forzado_sigue_traduciendo_a_409_rn_ord_60(self):
        """CONGELA comportamiento actual — RN-ORD-60/A-26
        (`tables_advanced.move_order:56-63`, spec.md Historia 4, escenario
        2): el índice único que originaba la colisión ya no existe en el
        modelo — ninguna secuencia de datos real dispara `IntegrityError`
        por sí sola. Se fuerza artificialmente (`db.flush` parcheado,
        Clarifications sesión 2026-08-17) y se confirma que el manejador
        `except IntegrityError` sigue presente y la traduce a 409."""
        db, origen, ts, order = self._seed_table_con_orden_activa(status="abierta")
        destino = fx.make_dining_table(db, status="libre")
        db.commit()

        with fx.force_flush_integrity_error(db):
            with self.assertRaises(HTTPException) as ctx:
                tables_advanced.move_order(db, order.id, destino.id)
        self.assertEqual(ctx.exception.status_code, 409)

    # --------------------------------------------------------- merge_orders (T045)

    def test_merge_orders_no_determinista_entre_grupos_preexistentes_rn_ord_63(self):
        """CONGELA comportamiento actual — RN-ORD-63/A-26
        (`tables_advanced.merge_orders:75-89`, spec.md Historia 4, escenario
        3): dos órdenes que ya pertenecen a dos `merged_group_id` distintos
        preexistentes → el grupo resultante es uno de los dos (`SELECT` sin
        `ORDER BY`, no determinista) — se documenta la propiedad, sin fijar
        un valor específico (research.md §3)."""
        db, table_a, ts_a, order_a = self._seed_table_con_orden_activa(status="abierta")
        db, table_b, ts_b, order_b = self._seed_table_con_orden_activa(db, status="abierta")

        group_a = tables_advanced.merge_orders(db, [order_a.id])["merged_group_id"]

        db, table_c, ts_c, order_c = self._seed_table_con_orden_activa(db, status="abierta")
        group_b = tables_advanced.merge_orders(db, [order_b.id, order_c.id])["merged_group_id"]

        self.assertNotEqual(group_a, group_b)

        resultado = tables_advanced.merge_orders(db, [order_a.id, order_b.id])
        self.assertIn(resultado["merged_group_id"], {group_a, group_b})

    # ----------------------------------------------------------- group_bill (T046)

    def test_group_bill_a01_camino_c_incluye_todos_los_status_sin_descuentos(self):
        """CONGELA comportamiento actual — A-01 camino C
        (`tables_advanced.group_bill:92-114`, spec.md Historia 4, escenario
        4): grupo fusionado con órdenes en status 'abierta', 'pagada' y
        'cancelada' → el total agregado incluye las tres sin excluir
        ninguna por status, y no aplica ningún descuento — tal como lo
        consume hoy `table.service.ts` del frontend."""
        # `merge_orders` rechaza órdenes ya terminales (`pagada`/`cancelada`):
        # las tres se fusionan mientras están 'abierta' y el status terminal
        # se fija después, directo en la fila — así se siembra el estado de
        # un grupo fusionado con pedidos ya cerrados, sin pasar por
        # `merge_orders` con ellos ya en terminal.
        db, table_a, ts_a, order_a = self._seed_table_con_orden_activa(status="abierta")
        db, table_b, ts_b, order_b = self._seed_table_con_orden_activa(db, status="abierta")
        db, table_c, ts_c, order_c = self._seed_table_con_orden_activa(db, status="abierta")

        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        fx.make_order_item(db, order_a, variant)
        fx.make_order_item(db, order_b, variant)
        fx.make_order_item(db, order_c, variant)

        promo = fx.make_promotion(db, type="percent", value=Decimal("50"), status="active")
        fx.make_promotion_target(db, promo, category_id=category.id)

        result = tables_advanced.merge_orders(db, [order_a.id, order_b.id, order_c.id])
        group_id = result["merged_group_id"]

        order_b.status = "pagada"
        order_c.status = "cancelada"
        db.commit()

        bill = tables_advanced.group_bill(db, group_id)

        order_ids = {o["order_id"] for o in bill["orders"]}
        self.assertEqual(order_ids, {order_a.id, order_b.id, order_c.id})
        self.assertEqual(bill["total"], PRECIO * 3)


if __name__ == "__main__":
    unittest.main()
