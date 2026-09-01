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
from pydantic import ValidationError
from sqlalchemy import select

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.orders import checkout
from app.api.v1.orders.schemas import BlockIn, CancelIn, CheckoutAndSendIn, PayIn
from app.api.v1.sales.schemas import PaymentIn
from app.api.v1.promotions import service as promotions
from app.models.audit_log import AuditLog
from app.models.cart import Cart
from app.models.inventory_movement import InventoryMovement
from app.models.order_payment_attempt import OrderPaymentAttempt
from app.models.sale import Sale

PRECIO = Decimal("10000")


class TestCheckout(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_order_con_receta(self, *, order_status="abierta", table_status="ocupada"):
        """Mesa ocupada + sesión + orden 'POS', con una variante que sí
        descuenta inventario (receta con stock de sobra)."""
        db = fx.new_session()
        table = fx.make_dining_table(db, status=table_status)
        ts = fx.make_table_session(db, table=table)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("2"))
        order = fx.make_customer_order(db, ts, status=order_status, channel="POS")
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

        promo = fx.make_promotion(db, status="active")
        fx.add_rule_to_promotion(
            db, promo, type="percent", value=Decimal("50"), min_qty=1,
            variants=[variant],
        )
        db.commit()

        resp = checkout.compute_bill(db, table.id)

        order_ids = {ol.order_id for ol in resp.orders}
        self.assertEqual(order_ids, {order_abierta.id, order_pagada.id})
        # `checkout.compute_bill` es código muerto que nunca aplicó descuentos.
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
        """CONGELA comportamiento actual (`checkout.promo_lines_for`), reescrito
        para el modelo por conjunto de variantes de la spec 063 (A-58…A-65,
        decisión de negocio en registro-de-anomalias.md; FR-003, FR-021).
        `promo_lines` ya no trae `product_id`/`category_id`/`presentation_id`;
        con una promoción `percent` sobre el conjunto que contiene la variante,
        `evaluate_variant_sets` devuelve el descuento; sin promoción, cero."""
        s = self._seed_order_con_receta()
        db, order, variant = s["db"], s["order"], s["variant"]
        fx.make_order_item(db, order, variant, quantity=2)
        db.commit()

        lines = checkout.order_sale_lines(db, order.id)
        promo_lines = checkout.promo_lines_for(db, lines)
        self.assertEqual(len(promo_lines), 1)
        self.assertEqual(promo_lines[0]["product_variant_id"], variant.id)
        self.assertEqual(promo_lines[0]["quantity"], 2)
        self.assertEqual(promo_lines[0]["unit_price"], PRECIO)
        self.assertIn("description", promo_lines[0])
        self.assertNotIn("category_id", promo_lines[0])
        self.assertNotIn("presentation_id", promo_lines[0])

        now = datetime.now(timezone.utc)
        r = promotions.evaluate_variant_sets(db, promo_lines, now)
        self.assertEqual(r.total, Decimal("0"))
        self.assertEqual(r.applied, [])

        promo = fx.make_promotion(db, status="active")
        fx.add_rule_to_promotion(
            db, promo, type="percent", value=Decimal("10"), min_qty=1,
            variants=[variant],
        )
        db.commit()

        r = promotions.evaluate_variant_sets(db, promo_lines, now)
        self.assertEqual(r.total, Decimal("2000.00"))
        self.assertEqual([a.promotion_id for a in r.applied], [promo.id])

    # ------------------------------------------------------------ pay_order (T026)

    def test_pay_order_construye_sale_real_con_promocion_activa(self):
        """CONGELA comportamiento actual (`checkout.pay_order`), reescrito para el
        modelo por conjunto de variantes (spec 063, A-58…A-65; FR-021): orden
        'bloqueada' + turno abierto + promoción `percent` activa sobre el
        conjunto → `pay_order` construye el `Sale` real con el descuento sumado,
        `promotion_id` poblado (una sola promoción) y `applied_promotions` con la
        entrada agregada."""
        s = self._seed_order_con_receta()
        db, order, variant = s["db"], s["order"], s["variant"]
        fx.make_order_item(db, order, variant, quantity=1)
        order.status = "bloqueada"
        promo = fx.make_promotion(db, status="active")
        fx.add_rule_to_promotion(
            db, promo, type="percent", value=Decimal("10"), min_qty=1,
            variants=[variant],
        )
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
        self.assertEqual(len(sale.applied_promotions), 1)
        self.assertEqual(sale.applied_promotions[0]["promotion_id"], str(promo.id))
        self.assertEqual(Decimal(sale.applied_promotions[0]["amount"]), esperado_descuento)
        db.refresh(order)
        self.assertEqual(order.status, "pagada")
        self.assertEqual(order.discount, esperado_descuento)
        self.assertEqual(order.applied_promotions[0]["promotion_id"], str(promo.id))

    def test_pay_order_dos_promociones_distintas_a29_promotion_id_none(self):
        """CONGELA comportamiento actual — A-29 (parcial), reescrito para la
        spec 063 (A-64: `applied_promotions` resuelve A-29). Dos promociones
        distintas descuentan líneas del mismo cobro → `Sale.promotion_id` queda
        `None` (como hoy) **pero** `applied_promotions` registra las dos."""
        s = self._seed_order_con_receta()
        db, order, category = s["db"], s["order"], s["category"]

        v1 = fx.make_variant(db, product=fx.make_product(db, category=category), price=Decimal("8000"))
        v2 = fx.make_variant(db, product=fx.make_product(db, category=category), price=Decimal("6000"))
        promo1 = fx.make_promotion(db, status="active")
        fx.add_rule_to_promotion(
            db, promo1, type="percent", value=Decimal("10"), min_qty=1, variants=[v1],
        )
        promo2 = fx.make_promotion(db, status="active")
        fx.add_rule_to_promotion(
            db, promo2, type="percent", value=Decimal("20"), min_qty=1, variants=[v2],
        )

        fx.make_order_item(db, order, v1, quantity=1)
        fx.make_order_item(db, order, v2, quantity=1)
        order.status = "bloqueada"

        register = fx.make_cash_register(db)
        shift = fx.make_cash_shift(db, register=register)
        method = fx.make_payment_method(db)
        db.commit()
        cashier = self._user()

        # promo1: 10% de 8000 = 800; promo2: 20% de 6000 = 1200.
        esperado_descuento = Decimal("2000.00")
        esperado_total = Decimal("14000") - esperado_descuento

        data = PayIn(cash_shift_id=shift.id, payments=[self._pago(method.id, esperado_total)])
        sale = checkout.pay_order(db, order.id, data, cashier)

        self.assertEqual(sale.discount, esperado_descuento)
        self.assertIsNone(sale.promotion_id)
        registradas = {e["promotion_id"] for e in sale.applied_promotions}
        self.assertEqual(registradas, {str(promo1.id), str(promo2.id)})

    # ---------------------------------------- spec 056: valor del domicilio en pay_order

    def test_pay_order_orden_delivery_suma_el_valor_del_domicilio_al_total(self):
        """FR-011: el total de la venta incluye el valor del domicilio de la
        orden asociada, y `Sale.delivery_fee` queda persistido."""
        s = self._seed_order_con_receta()
        db, order, variant = s["db"], s["order"], s["variant"]
        order.order_type = "DELIVERY"
        order.delivery_fee = Decimal("6000")
        fx.make_order_item(db, order, variant, quantity=1)
        order.status = "bloqueada"
        register = fx.make_cash_register(db)
        shift = fx.make_cash_shift(db, register=register)
        method = fx.make_payment_method(db)
        db.commit()
        cashier = self._user()

        esperado_total = PRECIO + Decimal("6000")
        data = PayIn(cash_shift_id=shift.id, payments=[self._pago(method.id, esperado_total)])
        sale = checkout.pay_order(db, order.id, data, cashier)

        self.assertEqual(sale.delivery_fee, Decimal("6000"))
        self.assertEqual(sale.total, esperado_total)

    def test_pay_order_orden_no_delivery_no_suma_ningun_valor_de_domicilio(self):
        """FR-012: sin efecto sobre el total de una orden que no es DELIVERY
        (no regresión)."""
        s = self._seed_order_con_receta()
        db, order, variant = s["db"], s["order"], s["variant"]
        fx.make_order_item(db, order, variant, quantity=1)
        order.status = "bloqueada"
        register = fx.make_cash_register(db)
        shift = fx.make_cash_shift(db, register=register)
        method = fx.make_payment_method(db)
        db.commit()
        cashier = self._user()

        data = PayIn(cash_shift_id=shift.id, payments=[self._pago(method.id, PRECIO)])
        sale = checkout.pay_order(db, order.id, data, cashier)

        self.assertEqual(sale.total, PRECIO)

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

        order = fx.make_customer_order(db, ts, status="recibida", channel="QR_MENU")
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
        order2 = fx.make_customer_order(db, ts, status="recibida", channel="QR_MENU")
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

        order = fx.make_customer_order(db, ts, status="abierta", channel="POS")
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

    def test_cancel_order_409_si_orden_ya_tiene_sale_spec_029(self):
        """Comportamiento NUEVO (spec 029, hotfix #4): igual que `void_item`
        (`test_orders_kitchen.py::test_void_item_409_si_orden_abierta_con_sale_camino_qr_mostrador_spec_029`),
        `cancel_order` debe reconocer un pedido ya cobrado por la `Sale`
        asociada, no por `status` — los caminos QR/mostrador dejan la orden
        en `"abierta"` a propósito tras el pago (research.md D2). Sin este
        chequeo, se podía "rechazar" un pedido ya pagado y dejar su `Sale`
        huérfana."""
        db = fx.new_session()
        ts = fx.make_table_session(db)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        order = fx.make_customer_order(db, ts, status="abierta", channel="POS")
        fx.make_order_item(db, order, variant, estado_cocina="pendiente")
        shift = fx.make_cash_shift(db)
        cashier = fx.make_user_double()
        sale = Sale(
            cash_shift_id=shift.id,
            customer_order_id=order.id,
            user_id=cashier.id,
            status="paid",
        )
        db.add(sale)
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            checkout.cancel_order(db, order.id, CancelIn(motivo="prueba"), cashier)
        self.assertEqual(ctx.exception.status_code, 409)
        db.refresh(order)
        self.assertEqual(order.status, "abierta")
        self.assertIsNotNone(
            db.execute(select(Sale).where(Sale.id == sale.id)).scalar_one_or_none()
        )

    # --------- cancel_order también resuelve el intento de pago (spec 044) ---------
    #
    # Comportamiento NUEVO (spec 044, revierte research.md D5 de spec 028 para pago
    # en efectivo y transferencia-sin-comprobante): rechazar un pedido con pago QR
    # pendiente ya no lo deja "pendiente" para siempre en una orden cancelada.

    def test_cancel_order_resuelve_intento_de_pago_pendiente_efectivo_spec_044(self):
        db = fx.new_session()
        ts = fx.make_table_session(db)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        order = fx.make_customer_order(db, ts, status="recibida", channel="QR_MENU")
        fx.make_order_item(db, order, variant, estado_cocina="pendiente")
        method = fx.make_payment_method(db, is_cash=True)
        attempt = fx.make_payment_attempt(db, order, method, status="pendiente")
        db.commit()
        user = self._user()

        resultado = checkout.cancel_order(db, order.id, CancelIn(motivo="no llegó"), user)
        self.assertEqual(resultado.status, "cancelada")

        db.refresh(attempt)
        self.assertEqual(attempt.status, "rechazado")
        self.assertEqual(attempt.rejection_reason, "no llegó")
        self.assertEqual(attempt.resolved_by_user_id, user.id)
        self.assertIsNotNone(attempt.resolved_at)

    def test_cancel_order_resuelve_intento_de_pago_pendiente_transferencia_sin_comprobante_spec_044(self):
        db = fx.new_session()
        ts = fx.make_table_session(db)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        order = fx.make_customer_order(db, ts, status="recibida", channel="QR_MENU")
        fx.make_order_item(db, order, variant, estado_cocina="pendiente")
        method = fx.make_payment_method(db, is_cash=False)
        attempt = fx.make_payment_attempt(
            db, order, method, status="pendiente", receipt_file_url=None
        )
        db.commit()
        user = self._user()

        checkout.cancel_order(db, order.id, CancelIn(motivo="se fue sin pagar"), user)

        db.refresh(attempt)
        self.assertEqual(attempt.status, "rechazado")
        self.assertEqual(attempt.rejection_reason, "se fue sin pagar")

    def test_cancel_order_sin_intento_de_pago_no_falla_spec_044(self):
        """Guarda de no-regresión: un pedido de mostrador/mesero (sin ningún
        `OrderPaymentAttempt`) se cancela exactamente igual que antes de spec 044."""
        db = fx.new_session()
        ts = fx.make_table_session(db)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        order = fx.make_customer_order(db, ts, status="abierta", channel="POS")
        fx.make_order_item(db, order, variant, estado_cocina="pendiente")
        db.commit()
        user = self._user()

        resultado = checkout.cancel_order(db, order.id, CancelIn(motivo="prueba"), user)
        self.assertEqual(resultado.status, "cancelada")

    def test_cancel_order_no_toca_intento_ya_confirmado_spec_044(self):
        """La búsqueda solo matchea `status='pendiente'` -- un intento ya
        resuelto (confirmado en otro pedido/momento) no se sobrescribe."""
        db = fx.new_session()
        ts = fx.make_table_session(db)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=PRECIO)
        order = fx.make_customer_order(db, ts, status="abierta", channel="POS")
        fx.make_order_item(db, order, variant, estado_cocina="pendiente")
        method = fx.make_payment_method(db, is_cash=True)
        confirmado = fx.make_payment_attempt(db, order, method, status="confirmado")
        db.commit()
        user = self._user()

        checkout.cancel_order(db, order.id, CancelIn(motivo="prueba"), user)

        db.refresh(confirmado)
        self.assertEqual(confirmado.status, "confirmado")
        self.assertIsNone(confirmado.rejection_reason)

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

    def test_release_table_borra_carritos_huerfanos_sin_ordenes_bloqueantes(self):
        """US2 escenario 2 (spec 039, Acceptance Scenario 2): "Liberar Mesa"
        (`release_table`) sin órdenes bloqueantes, con dos comensales ya
        cerrados de la misma sesión, cada uno con su propio Cart huérfano →
        la mesa queda 'libre' y ninguno de los dos Cart sigue existiendo.
        Cubre el edge case de spec.md "dos TableSession cerrándose en la
        misma operación" hasta donde lo permite este fixture: sembrar dos
        filas `TableSession` para la misma mesa colisiona con
        `idx_active_session_per_table` (único incondicional sobre SQLite,
        sin partial `WHERE` — `orders_fixtures.new_session` no lo remueve),
        así que se aísla el escenario con dos participantes de una sola
        sesión; `delete_orphan_carts` borra por `participant_id`, sin
        distinguir de qué sesión viene cada uno."""
        db = fx.new_session()
        table = fx.make_dining_table(db, status="ocupada")
        ts = fx.make_table_session(db, table=table)
        p1 = fx.make_participant(db, table_session=ts, status="closed")
        cart1 = fx.make_cart(db, participant=p1, status="abandonado")
        cart1_id = cart1.id
        p2 = fx.make_participant(db, table_session=ts, status="closed")
        cart2 = fx.make_cart(db, participant=p2, status="abandonado")
        cart2_id = cart2.id
        db.commit()

        result = checkout.release_table(db, table.id)

        self.assertEqual(result.status, "libre")
        self.assertIsNone(db.get(Cart, cart1_id))
        self.assertIsNone(db.get(Cart, cart2_id))

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
        order = fx.make_customer_order(db, ts, status="recibida", channel="POS")
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
        """Comportamiento nuevo (spec 028, T016), estado ampliado por spec 035
        (A-52, `registro-de-anomalias.md`): orden 'recibida' (nacida con
        `hold_for_payment=True`) + turno de caja abierto → `checkout_and_send`
        cobra (crea el `Sale`), descuenta inventario, y pasa la orden a
        'pagada' — todo en una sola llamada, sin pasar por
        `OrderPaymentAttempt` (eso es exclusivo del flujo QR). Antes de spec
        035 quedaba en 'abierta' pese a ya tener venta; ver research.md
        Decisión 1."""
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
        self.assertEqual(order.status, "pagada")
        self.assertEqual(order.version, 1)
        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 1)
        db.refresh(insumo)
        self.assertEqual(Decimal(insumo.current_stock), Decimal("998"))

    def test_checkout_and_send_orden_delivery_suma_el_valor_del_domicilio(self):
        """spec 056, FR-011: este es el camino real que sigue un pedido
        "Domicilio" creado desde la pantalla de creación manual
        (`hold_for_payment=True` → 'recibida' → checkout_and_send)."""
        s = self._seed_hold_order_con_receta()
        db, order, shift, method = s["db"], s["order"], s["shift"], s["method"]
        order.order_type = "DELIVERY"
        order.delivery_fee = Decimal("6000")
        db.commit()
        cashier = self._user()

        esperado_total = PRECIO + Decimal("6000")
        data = CheckoutAndSendIn(
            version=order.version, cash_shift_id=shift.id,
            payments=[self._pago(method.id, esperado_total)],
        )
        sale = checkout.checkout_and_send(db, order.id, data, cashier)

        self.assertEqual(sale.delivery_fee, Decimal("6000"))
        self.assertEqual(sale.total, esperado_total)

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

    def test_checkout_and_send_rechaza_descuento_manual_spec_029(self):
        """Comportamiento NUEVO (spec 029, Historia 2, FR-009/010/011): el
        único valor válido de `discount` en `checkout_and_send` es 0 — un
        descuento manual distinto de cero se rechaza en la propia validación
        del esquema (`le=0`), antes de que el handler llegue a ejecutarse."""
        s = self._seed_hold_order_con_receta()
        order, shift, method = s["order"], s["shift"], s["method"]

        with self.assertRaises(ValidationError):
            CheckoutAndSendIn(
                version=order.version, cash_shift_id=shift.id,
                discount=Decimal("5000"),
                payments=[self._pago(method.id, PRECIO)],
            )

    def test_checkout_and_send_discount_cero_u_omitido_sigue_igual(self):
        """Contraste del test anterior: `discount=0` (explícito u omitido,
        que es su valor por defecto) se comporta exactamente igual que
        antes de esta spec."""
        s = self._seed_hold_order_con_receta()
        db, order = s["db"], s["order"]
        shift, method = s["shift"], s["method"]
        cashier = self._user()

        data = CheckoutAndSendIn(
            version=order.version, cash_shift_id=shift.id,
            payments=[self._pago(method.id, PRECIO)],
        )
        sale = checkout.checkout_and_send(db, order.id, data, cashier)

        self.assertEqual(sale.total, PRECIO)


# spec 063 (A-58…A-65): la clase `TestCoexistenciaPromoPresentacion` (spec 040,
# no CONGELA) se elimina — el motor por conjunto no tiene "pool" ni
# reconciliación entre mecanismos (FR-014 garantiza una promoción por línea). Su
# cobertura equivalente vive en `test_promotions_service.py` (US2).


if __name__ == "__main__":
    unittest.main()
