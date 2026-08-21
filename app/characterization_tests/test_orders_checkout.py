"""CONGELA comportamiento actual: las 10 funciones públicas de
`app/api/v1/orders/checkout.py` (specs/017-caracterizacion-orders, Historia 2)
— el ciclo completo de cobro y cierre de una orden de mesa: bloqueo con lock
optimista, cuenta, pago (integración real con `sales.builder.build_sale`/
`ensure_open_shift`, sin mocks), confirmación (único punto de descuento del
flujo QR), cancelación con reversa asimétrica según estado de cocina, y
liberación de mesa.

Cierra el hueco de caracterización que las specs 015 (`cart`) y 016
(`table_sessions`) dejaron abierto al consumir estas mismas funciones como
dependencias externas (`cancel_order`, `TERMINAL`, `close_table_sessions`,
`order_sale_lines`, `promo_lines_for`) sin profundizar.

Incluye, citadas explícitamente:
  - A-01 (camino B, código muerto): `test_compute_bill_a01_camino_b_...`
  - A-29 (parcial): `test_pay_order_dos_combos_...a29...`
  - A-38 (RN-ORD-31): `test_close_table_sessions_no_valida_pendientes_rn_ord_31`
  - A-38 (RN-ORD-32): `test_order_sale_lines_...rn_ord_32...`

El caso de contraste directo de A-04 (`create_order` vs `add_item_to_table`)
vive en `test_orders_consolidation.py::test_create_order_contraste_a04_...`
(tasks.md T013/T033: se escribe una sola vez).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_orders_checkout -v
"""
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.orders import checkout
from app.api.v1.orders.schemas import BlockIn, CancelIn, CheckoutAndSendIn, PayIn
from app.api.v1.sales.schemas import PaymentIn
from app.api.v1.promotions import service as promotions
from app.models.audit_log import AuditLog
from app.models.inventory_movement import InventoryMovement
from app.models.sale import Sale

PRECIO = Decimal("10000")


class TestCheckout(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_order_con_receta(self, *, order_status="abierta", table_status="ocupada"):
        """Mesa ocupada + sesión + orden 'waiter', con una variante que sí
        descuenta inventario (receta con stock de sobra)."""
        db = fx.new_session()
        table = fx.make_dining_table(db, status=table_status)
        ts = fx.make_table_session(db, table=table)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("2"))
        order = fx.make_customer_order(db, ts, status=order_status, channel="waiter")
        db.commit()
        return dict(
            db=db, table=table, ts=ts, category=category, product=product,
            variant=variant, insumo=insumo, order=order,
        )

    def _pago(self, method_id, amount) -> PaymentIn:
        return PaymentIn(payment_method_id=method_id, amount=amount)

    def _user(self):
        return fx.make_user_double()

    # -------------------------------------------------------- block_order (T022)

    def test_block_order_bloquea_y_409_con_items_pendientes(self):
        """CONGELA comportamiento actual (`checkout.py:71-125`, spec.md
        Historia 2, escenario 1): orden 'abierta' sin ítems pendientes en
        cocina + versión correcta → pasa a 'bloqueada'; con al menos un
        ítem 'pendiente'/'en_preparacion' → 409 con el detalle de los ítems
        sin terminar."""
        s = self._seed_order_con_receta()
        db, order, variant = s["db"], s["order"], s["variant"]
        fx.make_order_item(db, order, variant, estado_cocina="listo")
        db.commit()

        result = checkout.block_order(db, order.id, BlockIn(version=0))
        self.assertEqual(result.status, "bloqueada")
        self.assertEqual(result.version, 1)

        s2 = self._seed_order_con_receta()
        db2, order2, variant2 = s2["db"], s2["order"], s2["variant"]
        fx.make_order_item(db2, order2, variant2, estado_cocina="pendiente")
        db2.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.block_order(db2, order2.id, BlockIn(version=0))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("items", ctx.exception.detail)

    # ------------------------------------------------------- compute_bill (T023)

    def test_compute_bill_a01_camino_b_incluye_pagada_sin_descuentos(self):
        """CONGELA comportamiento actual — A-01 camino B
        (`checkout.compute_bill:127-190`, sin caller de producción conocido):
        tabla con órdenes 'abierta'/'pagada'/'cancelada' → el total incluye
        'abierta' y 'pagada' (excluye 'cancelada'), sin aplicar ningún
        descuento aunque haya una promoción activa — código muerto pero
        peligroso si se reactiva (spec.md Historia 2, escenario 8)."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        ts = fx.make_table_session(db, table=table)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)

        order_abierta = fx.make_customer_order(db, ts, status="abierta")
        fx.make_order_item(db, order_abierta, variant, quantity=1)
        order_pagada = fx.make_customer_order(db, ts, status="pagada")
        fx.make_order_item(db, order_pagada, variant, quantity=1)
        order_cancelada = fx.make_customer_order(db, ts, status="cancelada")
        fx.make_order_item(db, order_cancelada, variant, quantity=1)

        promo = fx.make_promotion(db, type="percent", value=Decimal("50"), status="active")
        fx.make_promotion_target(db, promo, category_id=category.id)
        db.commit()

        resp = checkout.compute_bill(db, table.id)

        order_ids = {ol.order_id for ol in resp.orders}
        self.assertEqual(order_ids, {order_abierta.id, order_pagada.id})
        self.assertEqual(resp.total, PRECIO * 2)

    # --------------------------------------------------- order_sale_lines (T024)

    def test_order_sale_lines_camino_feliz_y_rn_ord_32_producto_o_variante_borrados(self):
        """CONGELA comportamiento actual — RN-ORD-32/A-38
        (`checkout.order_sale_lines:191-230`): camino feliz produce
        'Producto - Variante'; si el producto fue borrado antes de cobrar la
        descripción queda incompleta (solo el nombre de la variante); si la
        variante fue borrada, queda vacía."""
        s = self._seed_order_con_receta()
        db, order, category = s["db"], s["order"], s["category"]

        product_intacto = fx.make_product(db, category=category, name="Helado")
        variant_intacto = fx.make_variant(db, product=product_intacto, price=PRECIO, name="Grande")
        fx.make_order_item(db, order, variant_intacto)

        product_a_borrar = fx.make_product(db, category=category, name="Producto borrado")
        variant_prod_borrado = fx.make_variant(
            db, product=product_a_borrar, price=PRECIO, name="Variante huérfana"
        )
        fx.make_order_item(db, order, variant_prod_borrado)

        product_c = fx.make_product(db, category=category)
        variant_a_borrar = fx.make_variant(db, product=product_c, price=PRECIO)
        fx.make_order_item(db, order, variant_a_borrar)
        db.commit()

        # `db.delete()` del ORM cascadearía a las variantes (`Product.variants`,
        # `cascade="all, delete-orphan"`) — se borra por Core para dejar la
        # variante huérfana sin su producto, tal como puede quedar en producción
        # tras un borrado real de catálogo.
        from sqlalchemy import delete as sa_delete
        from app.models.product import Product

        db.execute(sa_delete(Product).where(Product.id == product_a_borrar.id))
        db.delete(variant_a_borrar)
        db.commit()

        lines = {l.product_variant_id: l for l in checkout.order_sale_lines(db, order.id)}

        self.assertEqual(lines[variant_intacto.id].description, "Helado - Grande")
        self.assertEqual(lines[variant_prod_borrado.id].description, "Variante huérfana")
        self.assertEqual(lines[variant_a_borrar.id].description, "")

    # ---------------------------------------------------- promo_lines_for (T025)

    def test_promo_lines_for_camino_feliz_y_sin_promocion_aplicable(self):
        """CONGELA comportamiento actual (`checkout.promo_lines_for:231-249`):
        con una promoción activa aplicable a las líneas dadas,
        `promotions.evaluate` sobre el resultado de `promo_lines_for`
        devuelve el descuento esperado; sin ninguna promoción activa,
        devuelve cero."""
        s = self._seed_order_con_receta()
        db, order, category, variant = s["db"], s["order"], s["category"], s["variant"]
        fx.make_order_item(db, order, variant, quantity=2)
        db.commit()

        lines = checkout.order_sale_lines(db, order.id)
        promo_lines = checkout.promo_lines_for(db, lines)
        self.assertEqual(len(promo_lines), 1)
        self.assertEqual(promo_lines[0]["category_id"], category.id)
        self.assertEqual(promo_lines[0]["quantity"], 2)
        self.assertEqual(promo_lines[0]["line_total"], PRECIO * 2)

        now = datetime.now(timezone.utc)
        sin_promo_discount, sin_promo_id = promotions.evaluate(db, promo_lines, now)
        self.assertEqual(sin_promo_discount, Decimal("0"))
        self.assertIsNone(sin_promo_id)

        promo = fx.make_promotion(db, type="percent", value=Decimal("10"), status="active")
        fx.make_promotion_target(db, promo, category_id=category.id)
        db.commit()

        con_promo_discount, con_promo_id = promotions.evaluate(db, promo_lines, now)
        self.assertEqual(con_promo_discount, Decimal("2000.00"))
        self.assertEqual(con_promo_id, promo.id)

    # ------------------------------------------------------------ pay_order (T026)

    def test_pay_order_construye_sale_real_con_promocion_activa(self):
        """CONGELA comportamiento actual (`checkout.pay_order:250-306`,
        spec.md Historia 2, escenario 2): orden 'bloqueada' + turno de caja
        abierto + promoción activa sin combos → `pay_order` construye el
        `Sale` vía `build_sale` real (sin mock), con el descuento de la
        promoción sumado y el turno de caja correcto."""
        s = self._seed_order_con_receta()
        db, order, category, variant = s["db"], s["order"], s["category"], s["variant"]
        fx.make_order_item(db, order, variant, quantity=1)
        order.status = "bloqueada"
        promo = fx.make_promotion(db, type="percent", value=Decimal("10"), status="active")
        fx.make_promotion_target(db, promo, category_id=category.id)
        register = fx.make_cash_register(db)
        shift = fx.make_cash_shift(db, register=register)
        method = fx.make_payment_method(db)
        db.commit()
        cashier = self._user()

        esperado_descuento = Decimal("1000.00")
        esperado_total = PRECIO - esperado_descuento

        data = PayIn(cash_shift_id=shift.id, payments=[self._pago(method.id, esperado_total)])
        sale = checkout.pay_order(db, order.id, data, cashier)

        self.assertEqual(sale.cash_shift_id, shift.id)
        self.assertEqual(sale.discount, esperado_descuento)
        self.assertEqual(sale.total, esperado_total)
        self.assertEqual(sale.promotion_id, promo.id)
        db.refresh(order)
        self.assertEqual(order.status, "pagada")

    def test_pay_order_dos_combos_distintos_a29_promotion_id_none(self):
        """CONGELA comportamiento actual — A-29 (parcial, `checkout.py:268-
        269`, spec.md Historia 2, escenario 3): líneas cobradas que usan dos
        combos distintos → el `Sale` resultante tiene `promotion_id=None`
        aunque el descuento monetario de ambos se sume correctamente."""
        s = self._seed_order_con_receta()
        db, order, category = s["db"], s["order"], s["category"]

        v1 = fx.make_variant(db, product=fx.make_product(db, category=category), price=Decimal("6000"))
        v2 = fx.make_variant(db, product=fx.make_product(db, category=category), price=Decimal("7000"))
        combo1 = fx.make_promotion(db, type="combo", value=Decimal("11000"), status="active")
        fx.make_combo_item(db, combo1, v1, quantity=1)
        fx.make_combo_item(db, combo1, v2, quantity=1)

        v3 = fx.make_variant(db, product=fx.make_product(db, category=category), price=Decimal("3000"))
        v4 = fx.make_variant(db, product=fx.make_product(db, category=category), price=Decimal("4000"))
        combo2 = fx.make_promotion(db, type="combo", value=Decimal("6000"), status="active")
        fx.make_combo_item(db, combo2, v3, quantity=1)
        fx.make_combo_item(db, combo2, v4, quantity=1)

        fx.make_order_item(db, order, v1, combo_id=combo1.id)
        fx.make_order_item(db, order, v2, combo_id=combo1.id)
        fx.make_order_item(db, order, v3, combo_id=combo2.id)
        fx.make_order_item(db, order, v4, combo_id=combo2.id)
        order.status = "bloqueada"

        register = fx.make_cash_register(db)
        shift = fx.make_cash_shift(db, register=register)
        method = fx.make_payment_method(db)
        db.commit()
        cashier = self._user()

        # combo1 ahorra 6000+7000-11000=2000; combo2 ahorra 3000+4000-6000=1000.
        esperado_descuento = Decimal("3000.00")
        esperado_total = Decimal("20000") - esperado_descuento

        data = PayIn(cash_shift_id=shift.id, payments=[self._pago(method.id, esperado_total)])
        sale = checkout.pay_order(db, order.id, data, cashier)

        self.assertEqual(sale.discount, esperado_descuento)
        self.assertIsNone(sale.promotion_id)

    # -------------------------------------------------------- confirm_order (T028)

    def test_confirm_order_descuenta_una_vez_y_stock_insuficiente_revierte(self):
        """CONGELA comportamiento actual (`checkout.confirm_order:366-390` +
        `checkout._confirm_order_impl:313-363`, spec.md Historia 2, escenario
        4): pedido 'recibida' con ítems válidos → pasa a 'abierta' y descuenta
        inventario exactamente una vez; con stock insuficiente de un insumo,
        la transacción entera revierte y el pedido sigue 'recibida'.

        Actualizado por spec 024-pagos-ordenes-mesa (FR-017, Principio III):
        `confirm_order` ahora exige un intento de pago `confirmado` antes de
        evaluar stock — cada orden de este test siembra uno (efectivo,
        confirmado) para poder seguir ejercitando exactamente el mismo
        camino de inventario que este test venía verificando.

        Actualizado de nuevo por spec 026-mejoras-ux-comanda (FR-001,
        Principio III): la lógica de `confirm_order` se extrajo a
        `_confirm_order_impl` (sin `commit`/`rollback` propios) para que
        `confirm_cash_payment_attempt`/`approve_payment_attempt` puedan
        invocarla dentro de su propia transacción y fusionar la confirmación
        del pago con el envío a cocina (spec 026, research.md Decisión 1).
        El comportamiento observable del endpoint público `confirm_order` que
        este test ejercita —llamado aquí directamente, no a través de un
        intento de pago— **no cambia**: mismas aserciones, sin ninguna
        modificación, siguen pasando sin tocar (evidencia de que el resto de
        la suite de characterization tests, 240 tests, sigue en verde tras el
        refactor)."""
        db = fx.new_session()
        ts = fx.make_table_session(db)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("5"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("2"))
        metodo = fx.make_payment_method(db, name="Efectivo", is_cash=True)

        order = fx.make_customer_order(db, ts, status="recibida", channel="qr")
        fx.make_order_item(db, order, variant, quantity=1, estado_cocina="pendiente")
        fx.make_payment_attempt(db, order, metodo, status="confirmado")
        db.commit()
        user = self._user()

        resultado = checkout.confirm_order(db, order.id, user)
        self.assertEqual(resultado.status, "abierta")
        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 1)
        db.refresh(insumo)
        self.assertEqual(Decimal(insumo.current_stock), Decimal("3"))

        # Stock insuficiente para un segundo pedido (necesita 10, sobran 3).
        order2 = fx.make_customer_order(db, ts, status="recibida", channel="qr")
        fx.make_order_item(db, order2, variant, quantity=5, estado_cocina="pendiente")
        fx.make_payment_attempt(db, order2, metodo, status="confirmado")
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.confirm_order(db, order2.id, user)
        self.assertEqual(ctx.exception.status_code, 400)
        db.refresh(order2)
        self.assertEqual(order2.status, "recibida")
        movimientos2 = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order2.id)
        ).scalars().all()
        self.assertEqual(movimientos2, [])

    # --------------------------------------------------------- cancel_order (T029)

    def test_cancel_order_reversa_asimetrica_segun_estado_cocina(self):
        """CONGELA comportamiento actual (`checkout.cancel_order:357-464`,
        spec.md Historia 2, escenario 5): orden con ítems en 'pendiente',
        'en_preparacion', 'listo', 'anulado' → solo el 'pendiente' genera una
        entrada real de inventario; 'en_preparacion'/'listo' no vuelven al
        stock (se registran como pérdida en `audit_logs`); 'anulado' ya se
        resolvió antes. Migra el escenario de reversa de
        `test_cancel_inventory.py`."""
        db = fx.new_session()
        ts = fx.make_table_session(db)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("2"))

        order = fx.make_customer_order(db, ts, status="abierta", channel="waiter")
        item_en_prep = fx.make_order_item(db, order, variant, estado_cocina="en_preparacion")
        item_listo = fx.make_order_item(db, order, variant, estado_cocina="listo")
        fx.make_order_item(db, order, variant, estado_cocina="pendiente")
        fx.make_order_item(db, order, variant, estado_cocina="anulado")
        db.commit()
        user = self._user()

        resultado = checkout.cancel_order(db, order.id, CancelIn(motivo="prueba"), user)
        self.assertEqual(resultado.status, "cancelada")

        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 1)
        self.assertEqual(movimientos[0].type, "in")

        logs = db.execute(
            select(AuditLog).where(
                AuditLog.entity_id == order.id, AuditLog.action == "order.cancel.loss"
            )
        ).scalars().all()
        self.assertEqual(len(logs), 1)
        perdidos_ids = {p["order_item_id"] for p in logs[0].payload["items_perdidos"]}
        self.assertEqual(perdidos_ids, {str(item_en_prep.id), str(item_listo.id)})

    # ----------------------------------------------------- close_participants (T030)

    def test_close_participants_cierra_activos_y_devuelve_conteo(self):
        """CONGELA comportamiento actual (`checkout.close_participants:466-
        493`): cierra los comensales 'open' de la sesión, abandona sus
        carritos abiertos, y devuelve cuántos cerró; no toca la sesión."""
        db = fx.new_session()
        ts = fx.make_table_session(db)
        ana = fx.make_participant(db, table_session=ts, status="open")
        beto = fx.make_participant(db, table_session=ts, status="open")
        fx.make_participant(db, table_session=ts, status="closed")
        cart_ana = fx.make_cart(db, participant=ana, status="abierto")
        db.commit()

        cerrados = checkout.close_participants(db, ts)
        self.assertEqual(cerrados, 2)
        db.refresh(ana)
        db.refresh(beto)
        db.refresh(ts)
        self.assertEqual(ana.status, "closed")
        self.assertEqual(beto.status, "closed")
        self.assertEqual(ts.status, "active")
        db.refresh(cart_ana)
        self.assertEqual(cart_ana.status, "abandonado")

    # -------------------------------------------------- close_table_sessions (T031)

    def test_close_table_sessions_no_valida_pendientes_rn_ord_31(self):
        """CONGELA comportamiento actual — RN-ORD-31/A-38
        (`checkout.close_table_sessions:495-527`, spec.md Historia 2,
        escenario 6): cierra en cascada las sesiones 'active' de la mesa sin
        validar por sí mismo que no haya órdenes pendientes — esa
        responsabilidad es del llamador. Incluye el escenario migrado de
        `test_session_ttl.py` que invoca esta función bajo el disparador del
        barrido automático (`closed_by=None`, `scheduler.py:140`)."""
        db = fx.new_session()
        table = fx.make_dining_table(db, status="ocupada")
        ts = fx.make_table_session(db, table=table)
        participant = fx.make_participant(db, table_session=ts, status="open")

        category = fx.make_category(db)
        variant = fx.make_variant(db, product=fx.make_product(db, category=category))
        order = fx.make_customer_order(db, ts, status="abierta")
        fx.make_order_item(db, order, variant)
        db.commit()

        cerradas = checkout.close_table_sessions(db, table.id, closed_by=None)
        db.commit()

        self.assertEqual(len(cerradas), 1)
        self.assertEqual(cerradas[0].id, ts.id)
        db.refresh(ts)
        self.assertEqual(ts.status, "closed")
        self.assertIsNone(ts.closed_by_user_id)
        db.refresh(order)
        self.assertEqual(order.status, "abierta")
        db.refresh(participant)
        self.assertEqual(participant.status, "closed")

    # -------------------------------------------------------- release_table (T032)

    def test_release_table_409_con_ordenes_activas_y_libera_sin_ellas(self):
        """CONGELA comportamiento actual (`checkout.release_table:528+`,
        spec.md Historia 2, escenario 6): mesa con órdenes activas sin
        cerrar → 409 con el detalle de las órdenes bloqueantes, sin liberar
        la mesa; sin órdenes activas → la mesa queda 'libre' y sus sesiones
        'active' se cierran en cascada (`close_table_sessions`,
        `close_participants`)."""
        db = fx.new_session()
        table = fx.make_dining_table(db, status="ocupada")
        ts = fx.make_table_session(db, table=table)
        order = fx.make_customer_order(db, ts, status="abierta")
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.release_table(db, table.id)
        self.assertEqual(ctx.exception.status_code, 409)
        db.refresh(table)
        self.assertEqual(table.status, "ocupada")

        order.status = "pagada"
        db.commit()

        result = checkout.release_table(db, table.id, closed_by=self._user())
        self.assertEqual(result.status, "libre")
        db.refresh(ts)
        self.assertEqual(ts.status, "closed")

    # -------------------------------------------------- checkout_and_send (T020)

    def _seed_hold_order_con_receta(self, *, stock=Decimal("1000"), recipe_qty=Decimal("2")):
        """Mesa ocupada + sesión + orden 'recibida' (como si hubiera nacido con
        `hold_for_payment=True`, T013), con una variante con receta y turno de
        caja abierto listo para cobrar."""
        db = fx.new_session()
        table = fx.make_dining_table(db, status="ocupada")
        ts = fx.make_table_session(db, table=table)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=stock)
        fx.make_recipe_item(db, variant, insumo, quantity=recipe_qty)
        order = fx.make_customer_order(db, ts, status="recibida", channel="waiter")
        fx.make_order_item(db, order, variant, quantity=1, estado_cocina="pendiente")
        register = fx.make_cash_register(db)
        shift = fx.make_cash_shift(db, register=register)
        method = fx.make_payment_method(db)
        db.commit()
        return dict(
            db=db, table=table, ts=ts, variant=variant, insumo=insumo,
            order=order, shift=shift, method=method,
        )

    def test_checkout_and_send_cobra_descuenta_y_abre_a_cocina(self):
        """Comportamiento nuevo (spec 028, T016): orden 'recibida' (nacida con
        `hold_for_payment=True`) + turno de caja abierto → `checkout_and_send`
        cobra (crea el `Sale`), descuenta inventario y pasa la orden a
        'abierta' — todo en una sola llamada, sin pasar por
        `OrderPaymentAttempt` (eso es exclusivo del flujo QR)."""
        s = self._seed_hold_order_con_receta()
        db, order, insumo = s["db"], s["order"], s["insumo"]
        shift, method = s["shift"], s["method"]
        cashier = self._user()

        data = CheckoutAndSendIn(
            version=order.version, cash_shift_id=shift.id,
            payments=[self._pago(method.id, PRECIO)],
        )
        sale = checkout.checkout_and_send(db, order.id, data, cashier)

        self.assertEqual(sale.total, PRECIO)
        self.assertEqual(sale.customer_name, "Consumidor Final")
        db.refresh(order)
        self.assertEqual(order.status, "abierta")
        self.assertEqual(order.version, 1)
        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 1)
        db.refresh(insumo)
        self.assertEqual(Decimal(insumo.current_stock), Decimal("998"))

    def test_checkout_and_send_version_desactualizada_409_sin_doble_venta(self):
        """Idempotencia / doble clic (spec 028, T016): una segunda llamada con
        la misma `version` ya vencida (la orden avanzó en la primera) es 409
        y no crea una segunda venta — mismo criterio que el lock optimista de
        `block_order`."""
        s = self._seed_hold_order_con_receta()
        db, order = s["db"], s["order"]
        shift, method = s["shift"], s["method"]
        cashier = self._user()

        data = CheckoutAndSendIn(
            version=order.version, cash_shift_id=shift.id,
            payments=[self._pago(method.id, PRECIO)],
        )
        checkout.checkout_and_send(db, order.id, data, cashier)

        with self.assertRaises(HTTPException) as ctx:
            checkout.checkout_and_send(db, order.id, data, cashier)
        self.assertEqual(ctx.exception.status_code, 409)

        ventas = db.execute(
            select(Sale).where(Sale.customer_order_id == order.id)
        ).scalars().all()
        self.assertEqual(len(ventas), 1)

    def test_checkout_and_send_stock_insuficiente_revierte_todo(self):
        """spec 028, T016: sin stock suficiente para descontar al enviar a
        cocina, la transacción entera revierte — la orden sigue 'recibida' y
        no queda ni venta ni movimiento de inventario a medias (mismo
        criterio de atomicidad que `_confirm_order_impl`, congelado en
        `test_confirm_order_descuenta_una_vez_y_stock_insuficiente_revierte`)."""
        s = self._seed_hold_order_con_receta(stock=Decimal("1"), recipe_qty=Decimal("2"))
        db, order, insumo = s["db"], s["order"], s["insumo"]
        shift, method = s["shift"], s["method"]
        cashier = self._user()

        data = CheckoutAndSendIn(
            version=order.version, cash_shift_id=shift.id,
            payments=[self._pago(method.id, PRECIO)],
        )
        with self.assertRaises(HTTPException) as ctx:
            checkout.checkout_and_send(db, order.id, data, cashier)
        self.assertEqual(ctx.exception.status_code, 400)

        db.refresh(order)
        self.assertEqual(order.status, "recibida")
        ventas = db.execute(
            select(Sale).where(Sale.customer_order_id == order.id)
        ).scalars().all()
        self.assertEqual(ventas, [])
        db.refresh(insumo)
        self.assertEqual(Decimal(insumo.current_stock), Decimal("1"))


if __name__ == "__main__":
    unittest.main()
