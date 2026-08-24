"""`app/api/v1/orders/service.py`: `create_order` (CONGELA comportamiento
actual, specs/017-caracterizacion-orders, Historia 5) más `order_has_sale`/
`paid_order_ids`, agregadas por spec 029 — estas dos últimas no son
characterization tests (no existía comportamiento previo que congelar), son
la verificación de la señal "pedido ya pagado" introducida por esa spec.

`create_order` es la comanda directa de mostrador/mesero que nace ya
`abierta` (confirmada) y descuenta inventario al crearse, porque no vuelve a
pasar por `confirm_order`.

El caso de contraste directo con **A-04** (`create_order` sí pasa `variant` a
`load_valid_options`, a diferencia de `consolidation.add_item_to_table`) ya
vive en `test_orders_consolidation.py::test_create_order_contraste_a04_...`
(tasks.md T013/T051: se escribe una sola vez).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_orders_service -v
"""
from decimal import Decimal
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.orders import service
from app.api.v1.orders.schemas import OrderChannel, OrderCreate, OrderItemIn
from app.models.inventory_movement import InventoryMovement
from app.models.sale import Sale

PRECIO = Decimal("10000")


class TestService(unittest.TestCase):
    def _seed_variant_con_receta_y_opciones(self, db):
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("2"))
        group = fx.make_option_group(db, min_select=1, max_select=1)
        option = fx.make_option(db, group=group, extra_price=Decimal("500"))
        fx.link_variant_group(db, variant, group, min_select=1, max_select=1)
        return variant, insumo, option

    # ------------------------------------------------------------ create_order (T048)

    def test_create_order_nace_abierta_y_descuenta_inventario(self):
        """CONGELA comportamiento actual (`service.py:37+`, spec.md Historia
        5, escenario 1): variante con receta y opciones válidas →
        `create_order` nace en status 'abierta' (no 'recibida') con el
        inventario ya descontado — no vuelve a pasar por `confirm_order`."""
        db = fx.new_session()
        variant, insumo, option = self._seed_variant_con_receta_y_opciones(db)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.COUNTER,
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1, option_ids=[option.id])],
        )
        order = service.create_order(db, data, uuid4())

        self.assertEqual(order.status, "abierta")
        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 1)
        db.refresh(insumo)
        self.assertEqual(Decimal(insumo.current_stock), Decimal("998"))

    # ----------------------------------------------- create_order sin receta (T049)

    def test_create_order_variante_sin_receta_rechaza(self):
        """CONGELA comportamiento actual: variante sin receta asociada → la
        guarda de `deduct_order_items` rechaza la creación con 409, migrando
        el caso de `test_receta_obligatoria.py` correspondiente a este
        camino (research.md §5, SC-007)."""
        db = fx.new_session()
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant_sin_receta = fx.make_variant(db, product=product, price=PRECIO)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.COUNTER,
            items=[OrderItemIn(product_variant_id=variant_sin_receta.id, quantity=1)],
        )
        with self.assertRaises(HTTPException) as ctx:
            service.create_order(db, data, uuid4())
        self.assertEqual(ctx.exception.status_code, 409)

    # ------------------------------------------- create_order abre sesión (T050)

    def test_create_order_abre_sesion_de_mesa_si_no_existe(self):
        """CONGELA comportamiento actual (spec.md Historia 5, escenario 3):
        mesa sin sesión de mesa activa → `create_order` con
        `dining_table_id` crea la sesión de mesa vía
        `consolidation.get_or_create_table_session_id` antes de crear la
        orden — congelando la dependencia real entre `service.py` y
        `consolidation.py`."""
        db = fx.new_session()
        table = fx.make_dining_table(db, status="libre")
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("1"))
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.WAITER,
            dining_table_id=table.id,
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1)],
        )
        order = service.create_order(db, data, uuid4())

        self.assertIsNotNone(order.table_session_id)
        db.refresh(table)
        self.assertEqual(table.status, "ocupada")

    # ------------------------------------- create_order hold_for_payment (T019)

    def test_create_order_hold_for_payment_nace_recibida_sin_descontar(self):
        """Comportamiento nuevo (spec 028, T013): con `hold_for_payment=True`
        la comanda nace 'recibida' (no 'abierta') y no descuenta inventario —
        el descuento se mueve a `checkout.checkout_and_send`, al cobrar."""
        db = fx.new_session()
        variant, insumo, option = self._seed_variant_con_receta_y_opciones(db)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.COUNTER,
            hold_for_payment=True,
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1, option_ids=[option.id])],
        )
        order = service.create_order(db, data, uuid4())

        self.assertEqual(order.status, "recibida")
        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(movimientos, [])
        db.refresh(insumo)
        self.assertEqual(Decimal(insumo.current_stock), Decimal("1000"))

    def test_create_order_hold_for_payment_con_channel_qr_400(self):
        """spec 028, T013: `hold_for_payment` es exclusivo de mostrador/mesero
        — combinado con `channel='qr'` es 400 (ese canal ya tiene su propio
        flujo 'recibida' vía `/cart/submit`)."""
        db = fx.new_session()
        variant, insumo, option = self._seed_variant_con_receta_y_opciones(db)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.QR,
            hold_for_payment=True,
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1, option_ids=[option.id])],
        )
        with self.assertRaises(HTTPException) as ctx:
            service.create_order(db, data, uuid4())
        self.assertEqual(ctx.exception.status_code, 400)

    # ------------------------ mezcla de orígenes QR/mostrador (spec 028, T014)

    def test_create_order_bloqueado_por_pedido_qr_activo_en_la_mesa_409(self):
        """spec 028, FR-013 (T014): una mesa no mezcla orígenes de pedido a la
        vez. Si ya hay un pedido QR activo (no terminal) en la sesión de
        mesa, una comanda de mostrador/mesero no puede abrirse encima —
        simetría directa de `cart.service.submit_cart` (T015)."""
        db = fx.new_session()
        table = fx.make_dining_table(db, status="ocupada")
        ts = fx.make_table_session(db, table=table)
        fx.make_customer_order(db, ts, channel="qr", status="recibida")
        variant, insumo, option = self._seed_variant_con_receta_y_opciones(db)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.WAITER,
            dining_table_id=table.id,
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1, option_ids=[option.id])],
        )
        with self.assertRaises(HTTPException) as ctx:
            service.create_order(db, data, uuid4())
        self.assertEqual(ctx.exception.status_code, 409)

    def test_create_order_permite_si_el_pedido_qr_ya_es_terminal(self):
        """Contraste del test anterior: un pedido QR 'pagada' ya no es
        'activo', así que no bloquea una comanda nueva de mostrador/mesero en
        la misma mesa."""
        db = fx.new_session()
        table = fx.make_dining_table(db, status="ocupada")
        ts = fx.make_table_session(db, table=table)
        fx.make_customer_order(db, ts, channel="qr", status="pagada")
        variant, insumo, option = self._seed_variant_con_receta_y_opciones(db)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.WAITER,
            dining_table_id=table.id,
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1, option_ids=[option.id])],
        )
        order = service.create_order(db, data, uuid4())
        self.assertEqual(order.status, "abierta")


class TestOrderHasSale(unittest.TestCase):
    """Spec 029 (D2/D3 de research.md): `order_has_sale`/`paid_order_ids` son
    la señal real de "este pedido ya está pagado" — no `CustomerOrder.status`,
    que nunca llega a `"pagada"` en los caminos QR/mostrador vigentes
    (`checkout_and_send`/`_confirm_order_impl` dejan la orden en `"abierta"`
    a propósito, con la `Sale` ya emitida)."""

    def _seed_order_con_sale(self, *, order_status: str) -> tuple:
        db = fx.new_session()
        ts = fx.make_table_session(db)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        order = fx.make_customer_order(db, ts, status=order_status, channel="waiter")
        shift = fx.make_cash_shift(db)
        cashier = fx.make_user_double()
        db.add(Sale(
            cash_shift_id=shift.id,
            customer_order_id=order.id,
            table_session_id=ts.id,
            user_id=cashier.id,
            status="paid",
        ))
        db.commit()
        return db, order

    def test_order_has_sale_true_sobre_orden_abierta_con_sale_camino_qr_mostrador(self):
        """El caso real que motivó la spec: `checkout_and_send`/
        `_confirm_order_impl` dejan la orden en 'abierta' con la `Sale` ya
        emitida — `order_has_sale` debe reconocerlo como pagado aunque
        `status` nunca haya llegado a 'pagada'."""
        db, order = self._seed_order_con_sale(order_status="abierta")

        self.assertTrue(service.order_has_sale(db, order.id))

    def test_order_has_sale_false_sobre_orden_abierta_sin_sale(self):
        db = fx.new_session()
        ts = fx.make_table_session(db)
        order = fx.make_customer_order(db, ts, status="abierta", channel="waiter")
        db.commit()

        self.assertFalse(service.order_has_sale(db, order.id))

    def test_paid_order_ids_resuelve_en_bloque_para_una_lista_de_pedidos(self):
        """Versión en bloque: una sola consulta para todo un listado, en vez
        de N — el pedido sin `Sale` no aparece en el resultado."""
        db, pagado = self._seed_order_con_sale(order_status="abierta")
        ts = fx.make_table_session(db)
        sin_pagar = fx.make_customer_order(db, ts, status="abierta", channel="waiter")
        db.commit()

        resultado = service.paid_order_ids(db, [pagado.id, sin_pagar.id])

        self.assertEqual(resultado, {pagado.id})

    def test_paid_order_ids_lista_vacia_no_consulta(self):
        db = fx.new_session()
        self.assertEqual(service.paid_order_ids(db, []), set())


class TestListOrdersActiveSessionsOnly(unittest.TestCase):
    """Spec 029, hotfix: `list_orders(active_sessions_only=True)` — un pedido
    ya pagado de una `TableSession` ya `'closed'` (visita anterior, mesa ya
    liberada y reabierta por QR) no debe reaparecer mezclado con la sesión
    activa de la misma mesa física. El caso real reportado en producción:
    liberar una mesa, volver a escanear su QR y ver los pedidos de la visita
    vieja junto al pedido nuevo."""

    def _seed(self, *, session_status: str, order_status: str, con_sale: bool):
        db = fx.new_session()
        ts = fx.make_table_session(db, status=session_status)
        order = fx.make_customer_order(db, ts, status=order_status, channel="waiter")
        if con_sale:
            shift = fx.make_cash_shift(db)
            cashier = fx.make_user_double()
            db.add(Sale(
                cash_shift_id=shift.id, customer_order_id=order.id,
                table_session_id=ts.id, user_id=cashier.id, status="paid",
            ))
        db.commit()
        return db, ts, order

    def test_excluye_pedido_pagado_de_sesion_ya_cerrada(self):
        db, _, order = self._seed(session_status="closed", order_status="abierta", con_sale=True)

        resultado = service.list_orders(db, active_sessions_only=True)

        self.assertNotIn(order.id, [o.id for o in resultado])

    def test_conserva_pedido_sin_pagar_de_sesion_ya_cerrada_caso_huerfano(self):
        db, _, order = self._seed(session_status="closed", order_status="abierta", con_sale=False)

        resultado = service.list_orders(db, active_sessions_only=True)

        self.assertIn(order.id, [o.id for o in resultado])

    def test_conserva_pedido_pagado_de_sesion_activa(self):
        db, _, order = self._seed(session_status="active", order_status="abierta", con_sale=True)

        resultado = service.list_orders(db, active_sessions_only=True)

        self.assertIn(order.id, [o.id for o in resultado])

    def test_conserva_pedido_sin_table_session_id_mostrador_puro(self):
        db = fx.new_session()
        ts = fx.make_table_session(db)  # solo para satisfacer la firma del fixture
        order = fx.make_customer_order(
            db, ts, table_session_id=None, dining_table_id=None,
            status="abierta", channel="counter",
        )
        db.commit()

        resultado = service.list_orders(db, active_sessions_only=True)

        self.assertIn(order.id, [o.id for o in resultado])

    def test_sin_el_parametro_el_resultado_no_cambia_respecto_a_hoy(self):
        db, _, order = self._seed(session_status="closed", order_status="abierta", con_sale=True)

        resultado = service.list_orders(db)

        self.assertIn(order.id, [o.id for o in resultado])


if __name__ == "__main__":
    unittest.main()
