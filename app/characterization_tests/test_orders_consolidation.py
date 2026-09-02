"""CONGELA comportamiento actual: las 5 funciones públicas de
`app/api/v1/orders/consolidation.py` (specs/017-caracterizacion-orders,
Historia 1) — la ruta de alta directa del mesero desde la terminal.

Documenta con el mayor cuidado de toda la spec **A-04**
(`consolidation.py:199`, dentro de `add_item_to_table`): `load_valid_options`
solo valida `min_select`/`max_select`/pertenencia de grupo cuando se le pasa
`variant`; `add_item_to_table` no se lo pasaba (a diferencia de
`service.create_order`, que sí), así que el camino real del mesero se saltaba
la validación de selección de opciones. La spec 017 congeló ese defecto tal
cual, sin corregirlo (FR-016).

**A-04 quedó corregido por `specs/020-correccion-validacion-opciones-mesero`**
(commit que cita esa decisión, Constitución Principio II): `add_item_to_table`
ahora pasa `variant=variant` a `load_valid_options`
(`consolidation.py:199`), igual que ya hacía `create_order` — antes aceptaba
en silencio una selección incompleta o que excediera `max_select`.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_orders_consolidation -v
"""
from decimal import Decimal
import unittest
from uuid import uuid4

from fastapi import HTTPException

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.orders import consolidation, service
from app.api.v1.orders.schemas import OrderCreate, OrderItemIn, OrderChannel
from app.api.v1.catalog.schemas import OptionSelectionIn
from app.models.customer_order import CustomerOrder
from app.models.sale import Sale

PRECIO = Decimal("10000")


class TestConsolidation(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_variant_con_receta(self, db, *, price=PRECIO):
        """Producto/variante con una receta válida (insumo con stock de sobra),
        para que `deduct_order_items` no bloquee el camino feliz por falta de
        receta."""
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=price)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        fx.make_recipe_item(db, variant, insumo, quantity=Decimal("1"))
        return category, product, variant, insumo

    def _seed_grupo_obligatorio_que_descuenta(self, db, variant):
        """Grupo `min_select=1` **que descuenta inventario** vía
        `quantity_per_option` del link (`grupos_que_descuentan`,
        `app/catalog_engine/pricing.py`): así la selección vacía es siempre
        bloqueante para `validate_option_selection`, sin depender del valor de
        `STRICT_OPTION_SELECTION` (que por defecto es `False`,
        `app/core/config.py:62`)."""
        group = fx.make_option_group(db, min_select=1, max_select=1)
        fx.link_variant_group(
            db, variant, group, min_select=1, max_select=1,
            quantity_per_option=Decimal("1"),
        )
        return group

    def _seed_grupo_tres_sabores_que_descuenta(self, db, variant):
        """Grupo `min_select=3`/`max_select=3` (`"elige 3 sabores"`) que
        descuenta inventario, con 4 opciones disponibles — 3 para la
        selección válida (Historia 1, escenario 2), 4 para la que excede el
        máximo (Historia 1, escenario 3)."""
        group = fx.make_option_group(db, min_select=3, max_select=3)
        fx.link_variant_group(
            db, variant, group, min_select=3, max_select=3,
            quantity_per_option=Decimal("1"),
        )
        options = [fx.make_option(db, group=group) for _ in range(4)]
        return group, options

    def _user(self):
        return fx.make_user_double()

    # ------------------------------------------------ add_item_to_table (T012)

    def test_add_item_to_table_a04_valida_seleccion_de_opciones_tras_la_correccion(self):
        """CONGELA comportamiento corregido — A-04 (`consolidation.py:199`,
        spec 020, `registro-de-anomalias.md` "Tratamiento acordado"): una
        variante con un grupo de opciones obligatorio (`min_select=1`) y
        ninguna opción seleccionada se rechaza con 422, porque
        `add_item_to_table` ahora pasa `variant` a `load_valid_options`,
        igual que ya hacía `create_order`."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        _, _, variant, _ = self._seed_variant_con_receta(db)
        self._seed_grupo_obligatorio_que_descuenta(db, variant)
        db.commit()
        user = self._user()

        data = OrderItemIn(product_variant_id=variant.id, quantity=1, options=[])
        with self.assertRaises(HTTPException) as ctx:
            consolidation.add_item_to_table(db, table.id, data, user)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_add_item_to_table_seleccion_completa_se_acepta_historia_1_escenario_2(self):
        """Historia 1, escenario 2 (spec 020): la misma variante con un grupo
        `min_select=3`/`max_select=3`, seleccionando los 3 sabores correctos,
        se agrega normalmente — sin cambio frente al comportamiento de
        siempre para una selección completa."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        _, _, variant, _ = self._seed_variant_con_receta(db)
        _, options = self._seed_grupo_tres_sabores_que_descuenta(db, variant)
        db.commit()
        user = self._user()

        data = OrderItemIn(
            product_variant_id=variant.id, quantity=1,
            options=[OptionSelectionIn(option_id=o.id) for o in options[:3]],
        )
        order = consolidation.add_item_to_table(db, table.id, data, user)

        self.assertEqual(len(order.items), 1)
        self.assertEqual(order.items[0].product_variant_id, variant.id)
        self.assertEqual(len(order.items[0].options), 3)

    def test_add_item_to_table_excede_maximo_del_grupo_rechaza_historia_1_escenario_3(self):
        """Historia 1, escenario 3 (spec 020): la misma variante y grupo
        (`max_select=3`), seleccionando 4 sabores, se rechaza con 422 — el
        mismo mecanismo de `min_select`/`max_select` cubre tanto elegir de
        menos (escenario 1) como de más."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        _, _, variant, _ = self._seed_variant_con_receta(db)
        _, options = self._seed_grupo_tres_sabores_que_descuenta(db, variant)
        db.commit()
        user = self._user()

        data = OrderItemIn(
            product_variant_id=variant.id, quantity=1,
            options=[OptionSelectionIn(option_id=o.id) for o in options],
        )
        with self.assertRaises(HTTPException) as ctx:
            consolidation.add_item_to_table(db, table.id, data, user)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_add_item_to_table_y_create_order_convergen_tras_la_correccion_historia_2_escenario_1(self):
        """Historia 2, escenario 1 (spec 020, research.md Decisión 3): el
        mismo escenario de selección vacía en un grupo `min_select=1` que
        descuenta inventario, ejecutado por separado vía `add_item_to_table`
        y vía `create_order`, produce el mismo `status_code` en ambos —
        cierra la divergencia que motivaba A-04."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        _, _, variant, _ = self._seed_variant_con_receta(db)
        self._seed_grupo_obligatorio_que_descuenta(db, variant)
        db.commit()
        user = self._user()

        data_add = OrderItemIn(product_variant_id=variant.id, quantity=1, options=[])
        with self.assertRaises(HTTPException) as ctx_add:
            consolidation.add_item_to_table(db, table.id, data_add, user)

        data_create = OrderCreate(
            channel=OrderChannel.POS,
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1, options=[])],
        )
        with self.assertRaises(HTTPException) as ctx_create:
            service.create_order(db, data_create, uuid4())

        self.assertEqual(ctx_add.exception.status_code, ctx_create.exception.status_code)

    # ------------------------------------------ create_order, contraste (T013)

    def test_create_order_contraste_a04_si_valida_seleccion_de_opciones(self):
        """CONGELA comportamiento actual — caso de contraste directo de A-04
        (FR-003): el mismo escenario del test anterior, pero llamando a
        `service.create_order` — que sí pasa `variant` a `load_valid_options`
        (`service.py:102`) — falla con 422, documentando el contraste exacto
        entre los dos caminos que motiva A-04. Cubre también T033/T051: este
        es el único fichero donde vive el caso de contraste (Notes de
        tasks.md)."""
        db = fx.new_session()
        _, _, variant, _ = self._seed_variant_con_receta(db)
        self._seed_grupo_obligatorio_que_descuenta(db, variant)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.POS,
            items=[OrderItemIn(product_variant_id=variant.id, quantity=1, options=[])],
        )
        with self.assertRaises(HTTPException) as ctx:
            service.create_order(db, data, uuid4())
        self.assertEqual(ctx.exception.status_code, 422)

    # ------------------------------------------- active_table_session_id (T014)

    def test_active_table_session_id_con_y_sin_sesion(self):
        """CONGELA comportamiento actual (`consolidation.py:34-44`): mesa con
        sesión `active` devuelve su id; mesa sin sesión devuelve `None`."""
        db = fx.new_session()
        table_con = fx.make_dining_table(db)
        ts = fx.make_table_session(db, table=table_con, status="active")
        table_sin = fx.make_dining_table(db)
        db.commit()

        self.assertEqual(consolidation.active_table_session_id(db, table_con.id), ts.id)
        self.assertIsNone(consolidation.active_table_session_id(db, table_sin.id))

    # --------------------------------------- get_or_create_table_session_id (T015)

    def test_get_or_create_table_session_id_crea_y_reutiliza(self):
        """CONGELA comportamiento actual (`consolidation.py:46-67`): mesa sin
        sesión abierta crea una nueva y marca la mesa `ocupada`; mesa con
        sesión ya `active` devuelve la existente sin duplicar."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        db.commit()

        session_id = consolidation.get_or_create_table_session_id(db, table.id)
        db.commit()
        db.refresh(table)
        self.assertIsNotNone(session_id)
        self.assertEqual(table.status, "ocupada")

        session_id_2 = consolidation.get_or_create_table_session_id(db, table.id)
        self.assertEqual(session_id_2, session_id)

    # -------------------------------------------- get_or_create_open_order (T016)

    def test_get_or_create_open_order_crea_y_reutiliza(self):
        """CONGELA comportamiento actual (`consolidation.py:69-104`): mesa sin
        orden `abierta` de consolidación crea una nueva vía
        `get_or_create_table_session_id`; con una ya existente, la reutiliza
        sin duplicar. Spec 055 (research.md D2): el canal ahora es `POS`
        (estandarizado) y la distinción interna que antes vivía en
        `channel == 'waiter'` vive en `is_consolidation_order`."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        db.commit()
        user_id = uuid4()

        order = consolidation.get_or_create_open_order(db, table.id, user_id)
        db.commit()
        self.assertEqual(order.status, "abierta")
        self.assertEqual(order.channel, "POS")
        self.assertTrue(order.is_consolidation_order)
        self.assertEqual(order.order_type, "DINE_IN")
        self.assertIsNotNone(order.table_session_id)

        order_2 = consolidation.get_or_create_open_order(db, table.id, user_id)
        self.assertEqual(order_2.id, order.id)

    # --------------------------------- add_item_to_table abre sobre la marcha (T017)

    def test_add_item_to_table_abre_sesion_y_orden_sobre_la_marcha(self):
        """CONGELA comportamiento actual (spec.md Historia 1, escenario 3):
        mesa sin sesión de mesa abierta ni orden abierta → `add_item_to_table`
        crea ambas y el ítem queda asociado a ellas."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        _, _, variant, _ = self._seed_variant_con_receta(db)
        db.commit()
        user = self._user()

        data = OrderItemIn(product_variant_id=variant.id, quantity=1)
        order = consolidation.add_item_to_table(db, table.id, data, user)

        self.assertEqual(order.status, "abierta")
        self.assertEqual(order.channel, "POS")
        self.assertTrue(order.is_consolidation_order)
        self.assertIsNotNone(order.table_session_id)
        self.assertEqual(len(order.items), 1)

    # --------------------------------------------------- consolidate_table (T018)

    def test_consolidate_table_consolida_carritos_en_orden_existente(self):
        """CONGELA comportamiento actual (spec.md Historia 1, escenario 4):
        orden `abierta` de consolidación ya existente con ítems previos +
        comensales con carritos abiertos → `consolidate_table` agrega las
        líneas de los carritos a la orden existente sin duplicar los ítems ya
        presentes. Documenta también la ausencia de idempotencia: invocado
        una segunda vez sin carritos abiertos nuevos, responde 409 en vez de
        no-op."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        ts = fx.make_table_session(db, table=table)
        ana = fx.make_participant(db, table_session=ts, display_name="Ana")
        beto = fx.make_participant(db, table_session=ts, display_name="Beto")

        _, _, variant, _ = self._seed_variant_con_receta(db)

        order = fx.make_customer_order(
            db, ts, channel="POS", is_consolidation_order=True, status="abierta",
        )
        item_previo = fx.make_order_item(db, order, variant, estado_cocina="pendiente")

        cart_ana = fx.make_cart(db, participant=ana)
        fx.make_cart_item(db, cart_ana, variant, quantity=2)
        cart_beto = fx.make_cart(db, participant=beto)
        fx.make_cart_item(db, cart_beto, variant, quantity=1)

        db.commit()
        user = self._user()

        result = consolidation.consolidate_table(db, table.id, user)

        self.assertEqual(result.id, order.id)
        item_ids = {it.id for it in result.items}
        self.assertIn(item_previo.id, item_ids)
        # 1 previo + 1 de Ana + 1 de Beto = 3 líneas, sin duplicar el previo.
        self.assertEqual(len(result.items), 3)
        nuevas = [it for it in result.items if it.id != item_previo.id]
        self.assertTrue(all(it.estado_cocina == "pendiente" for it in nuevas))
        db.refresh(cart_ana)
        db.refresh(cart_beto)
        self.assertEqual(cart_ana.status, "confirmado")
        self.assertEqual(cart_beto.status, "confirmado")

        # Segunda invocación: sin carritos 'abierto' con ítems, no hay nada que
        # consolidar — responde 409 en vez de devolver la orden sin cambios.
        with self.assertRaises(HTTPException) as ctx:
            consolidation.consolidate_table(db, table.id, user)
        self.assertEqual(ctx.exception.status_code, 409)

    # ---------------------------------------- add_item_to_table combo (T019)

    # spec 063 (FR-024, A-61): `test_add_item_to_table_combo_expande_componentes_a_precio_normal`
    # se elimina — el mecanismo de combo se retira; `OrderItemIn` ya no acepta `combo_id`.

    # ------------------------------------- add_item_to_table sin receta (T020)

    def test_add_item_to_table_variante_sin_receta_rechaza(self):
        """CONGELA comportamiento actual: variante sin receta asociada vía
        `add_item_to_table` → la guarda de `deduct_order_items` rechaza la
        creación con 409, migrando el caso de `test_receta_obligatoria.py`
        correspondiente a este camino (research.md §5, SC-007)."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant_sin_receta = fx.make_variant(db, product=product, price=PRECIO)
        db.commit()
        user = self._user()

        data = OrderItemIn(product_variant_id=variant_sin_receta.id, quantity=1)
        with self.assertRaises(HTTPException) as ctx:
            consolidation.add_item_to_table(db, table.id, data, user)
        self.assertEqual(ctx.exception.status_code, 409)

        # La orden no quedó creada a medias.
        orders = db.query(CustomerOrder).all()
        self.assertEqual(orders, [])

    # ------------------------- spec 055, research.md D2: no reabrir lo cobrado

    def test_get_or_create_open_order_no_reabre_una_comanda_de_mostrador_ya_cobrada(self):
        """El hallazgo central del plan de spec 055: fusionar 'counter' y
        'waiter' en un único canal 'POS' no puede hacer que el mesero, al
        agregar un ítem directo a la mesa, reabra por accidente una comanda
        de mostrador que ya se cobró (`checkout_and_send` deja la orden en
        'abierta' a propósito, con la venta ya emitida) — `is_consolidation_order`
        es justo lo que evita esa colisión."""
        db = fx.new_session()
        table = fx.make_dining_table(db)
        ts = fx.make_table_session(db, table=table)
        db.commit()

        # Comanda de mostrador ya cobrada: status='abierta' + Sale emitida,
        # is_consolidation_order=False (no la abrió el mesero).
        comanda_cobrada = fx.make_customer_order(
            db, ts, dining_table_id=table.id, channel="POS",
            is_consolidation_order=False, status="abierta",
        )
        shift = fx.make_cash_shift(db)
        cajero = fx.make_user_double()
        db.add(Sale(
            cash_shift_id=shift.id, customer_order_id=comanda_cobrada.id,
            table_session_id=ts.id, user_id=cajero.id, status="paid",
        ))
        db.commit()

        # El mesero agrega un ítem directo a la misma mesa.
        order = consolidation.get_or_create_open_order(db, table.id, uuid4())

        self.assertNotEqual(order.id, comanda_cobrada.id)
        self.assertTrue(order.is_consolidation_order)


if __name__ == "__main__":
    unittest.main()
