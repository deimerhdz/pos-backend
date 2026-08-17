"""CONGELA comportamiento actual: las 3 funciones públicas de
`app/api/v1/orders/kitchen.py` (specs/017-caracterizacion-orders, Historia 3)
— el ciclo de vida de preparación y anulación de un ítem, independiente del
status de pago de la orden.

Documenta que ninguna de las tres valida el `status` de la `CustomerOrder`
padre salvo la validación parcial de `mark_order_ready` (**A-16**), y que sus
transiciones internas están cerradas a una lista blanca sin vía genérica de
asignación libre (**A-25 [PROTEGIDA]**), invariante que también verifica sobre
las otras cuatro funciones públicas de `checkout.py` que mutan estado
(`block_order`, `confirm_order`, `pay_order`, `cancel_order`).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_orders_kitchen -v
"""
from decimal import Decimal
import unittest

from fastapi import HTTPException

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.orders import checkout, kitchen
from app.api.v1.orders.schemas import BlockIn, CancelIn, KitchenTransitionIn, OrderItemIn, PayIn, VoidItemIn
from app.api.v1.sales.schemas import PaymentIn

PRECIO = Decimal("10000")


class TestKitchen(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_item(self, *, order_status="abierta", estado_cocina="pendiente"):
        db = fx.new_session()
        ts = fx.make_table_session(db)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("1"))
        order = fx.make_customer_order(db, ts, status=order_status, channel="waiter")
        item = fx.make_order_item(db, order, variant, estado_cocina=estado_cocina)
        db.commit()
        return dict(db=db, ts=ts, order=order, item=item, variant=variant, insumo=insumo)

    def _user(self):
        return fx.make_user_double()

    # ------------------------------------------------- transition_kitchen (T035)

    def test_transition_kitchen_salto_directo_y_retroceso_rechazado(self):
        """CONGELA comportamiento actual — A-25 [PROTEGIDA]
        (`kitchen.py:43-60`, spec.md Historia 3, escenario 1): un ítem
        'pendiente' salta directo a 'listo' (el botón de un toque de la
        terminal); un ítem 'listo' que intenta retroceder a 'pendiente'
        responde 409 — la lista blanca `_ALLOWED` solo permite avanzar."""
        s = self._seed_item(estado_cocina="pendiente")
        db, item = s["db"], s["item"]

        actualizado = kitchen.transition_kitchen(
            db, item.id, KitchenTransitionIn(estado_cocina="listo")
        )
        self.assertEqual(actualizado.estado_cocina, "listo")

        with self.assertRaises(HTTPException) as ctx:
            kitchen.transition_kitchen(db, item.id, KitchenTransitionIn(estado_cocina="pendiente"))
        self.assertEqual(ctx.exception.status_code, 409)

    # ----------------------------- transition_kitchen / void_item ignoran orden (T036)

    def test_transition_kitchen_y_void_item_no_validan_status_de_la_orden_a16(self):
        """CONGELA comportamiento actual — A-16 (`kitchen.py:43-60,93-176`,
        spec.md Historia 3, escenario 2): orden ya 'pagada' con ítems aún
        'pendiente' → tanto `transition_kitchen` como `void_item` se
        ejecutan igual, sin ningún error por el status de la orden padre."""
        s = self._seed_item(order_status="pagada", estado_cocina="pendiente")
        db, item = s["db"], s["item"]

        actualizado = kitchen.transition_kitchen(
            db, item.id, KitchenTransitionIn(estado_cocina="en_preparacion")
        )
        self.assertEqual(actualizado.estado_cocina, "en_preparacion")

        s2 = self._seed_item(order_status="pagada", estado_cocina="pendiente")
        db2, item2 = s2["db"], s2["item"]
        user = self._user()
        resultado = kitchen.void_item(db2, item2.id, VoidItemIn(motivo="prueba"), user)
        anulado = next(it for it in resultado.items if it.id == item2.id)
        self.assertEqual(anulado.estado_cocina, "anulado")

    # ---------------------------------------------- mark_order_ready valida (T037)

    def test_mark_order_ready_409_si_orden_pagada_contraste_a16(self):
        """CONGELA comportamiento actual — A-16 (`kitchen.py:63-90`, spec.md
        Historia 3, escenario 3): la misma orden 'pagada' con ítems
        'pendiente' → `mark_order_ready` sí responde 409 citando que la
        orden ya es terminal, a diferencia de `transition_kitchen`/
        `void_item` (T036)."""
        s = self._seed_item(order_status="pagada", estado_cocina="pendiente")
        db, order = s["db"], s["order"]

        with self.assertRaises(HTTPException) as ctx:
            kitchen.mark_order_ready(db, order.id)
        self.assertEqual(ctx.exception.status_code, 409)

    # ------------------------------------- mark_order_ready sí pasa en bloqueada (T038)

    def test_mark_order_ready_pasa_items_en_orden_bloqueada(self):
        """CONGELA comportamiento actual (`kitchen.py:63-90`, spec.md
        Historia 3, escenario 4): orden 'bloqueada' (no terminal de pago)
        con ítems en curso → `mark_order_ready` los pasa a 'listo' sin
        error — la validación bloquea solo 'pagada'/'cancelada', no
        'bloqueada' (la porción pendiente de A-16)."""
        s = self._seed_item(order_status="bloqueada", estado_cocina="en_preparacion")
        db, order = s["db"], s["order"]

        actualizada, cambiados = kitchen.mark_order_ready(db, order.id)
        self.assertEqual(len(cambiados), 1)
        self.assertTrue(all(it.estado_cocina == "listo" for it in cambiados))

    # ------------------------------------------------------------ void_item (T039)

    def test_void_item_anula_y_reemplaza_con_reversa_y_nuevo_descuento(self):
        """CONGELA comportamiento actual (`kitchen.py:93-176`, spec.md
        Historia 3, escenario 5): ítem 'pendiente' con `data.replacement`
        válido → el original queda 'anulado' (con reversa de inventario, por
        ser 'pendiente') y se crea uno nuevo 'pendiente' con `void_de`
        apuntando al original y su propio descuento de inventario."""
        from sqlalchemy import select
        from app.models.inventory_movement import InventoryMovement

        s = self._seed_item(estado_cocina="pendiente")
        db, item, insumo = s["db"], s["item"], s["insumo"]
        stock_inicial = Decimal(insumo.current_stock)
        user = self._user()

        resultado = kitchen.void_item(
            db, item.id,
            VoidItemIn(
                motivo="cambio de sabor",
                replacement=OrderItemIn(product_variant_id=s["variant"].id, quantity=1),
            ),
            user,
        )

        original = next(it for it in resultado.items if it.id == item.id)
        self.assertEqual(original.estado_cocina, "anulado")

        nuevo = next(it for it in resultado.items if it.void_de == item.id)
        self.assertEqual(nuevo.estado_cocina, "pendiente")

        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == item.order_id)
        ).scalars().all()
        tipos = sorted(m.type for m in movimientos)
        self.assertEqual(tipos, ["in", "out"])
        # Reversa del original ('in') + descuento del nuevo ('out'), cantidad
        # idéntica (1 unidad de receta): stock termina igual que al empezar.
        db.refresh(insumo)
        self.assertEqual(Decimal(insumo.current_stock), stock_inicial)

    # ----------------------------------- invariante A-25 [PROTEGIDA] de referencia (T040)

    def test_a25_protegida_ninguna_funcion_acepta_transicion_fuera_de_su_lista_blanca(self):
        """CONGELA comportamiento actual — A-25 [PROTEGIDA] (spec.md Historia
        3, escenario 6): de las siete funciones públicas de estos cinco
        ficheros que mutan `status`/`estado_cocina` (`block_order`,
        `confirm_order`, `pay_order`, `cancel_order`, `transition_kitchen`,
        `mark_order_ready`, `void_item`), cada una impone su propia
        transición validada — ninguna acepta una arbitraria sin pasar por su
        propia guarda. Este test es de referencia: reutiliza los casos ya
        congelados en test_orders_checkout.py (T022, T026, T028, T029) y
        arriba (T035, T037, T039), verificando el contraste directo aquí
        para las tres funciones de `kitchen.py`."""
        # transition_kitchen: 'anulado' no está en ningún valor de _ALLOWED.
        s = self._seed_item(estado_cocina="anulado")
        db, item = s["db"], s["item"]
        with self.assertRaises(HTTPException) as ctx:
            kitchen.transition_kitchen(db, item.id, KitchenTransitionIn(estado_cocina="listo"))
        self.assertEqual(ctx.exception.status_code, 409)

        # mark_order_ready: orden ya 'cancelada' es terminal.
        s2 = self._seed_item(order_status="cancelada", estado_cocina="pendiente")
        with self.assertRaises(HTTPException) as ctx2:
            kitchen.mark_order_ready(s2["db"], s2["order"].id)
        self.assertEqual(ctx2.exception.status_code, 409)

        # void_item: un ítem ya anulado no se puede volver a anular.
        s3 = self._seed_item(estado_cocina="anulado")
        with self.assertRaises(HTTPException) as ctx3:
            kitchen.void_item(s3["db"], s3["item"].id, VoidItemIn(motivo="x"), self._user())
        self.assertEqual(ctx3.exception.status_code, 409)

        # block_order: orden 'bloqueada' no puede volver a bloquearse.
        s4 = self._seed_item(order_status="bloqueada", estado_cocina="listo")
        with self.assertRaises(HTTPException) as ctx4:
            checkout.block_order(s4["db"], s4["order"].id, BlockIn(version=0))
        self.assertEqual(ctx4.exception.status_code, 409)

        # confirm_order: orden 'abierta' no está en 'recibida'.
        s5 = self._seed_item(order_status="abierta", estado_cocina="listo")
        with self.assertRaises(HTTPException) as ctx5:
            checkout.confirm_order(s5["db"], s5["order"].id, self._user())
        self.assertEqual(ctx5.exception.status_code, 409)

        # pay_order: orden 'abierta' no está 'bloqueada'.
        s6 = self._seed_item(order_status="abierta", estado_cocina="listo")
        db6, order6 = s6["db"], s6["order"]
        register = fx.make_cash_register(db6)
        shift = fx.make_cash_shift(db6, register=register)
        method = fx.make_payment_method(db6)
        db6.commit()
        with self.assertRaises(HTTPException) as ctx6:
            checkout.pay_order(
                db6, order6.id,
                PayIn(cash_shift_id=shift.id, payments=[PaymentIn(payment_method_id=method.id, amount=PRECIO)]),
                self._user(),
            )
        self.assertEqual(ctx6.exception.status_code, 409)

        # cancel_order: orden ya 'pagada' es terminal.
        s7 = self._seed_item(order_status="pagada", estado_cocina="listo")
        with self.assertRaises(HTTPException) as ctx7:
            checkout.cancel_order(s7["db"], s7["order"].id, CancelIn(motivo="x"), self._user())
        self.assertEqual(ctx7.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
