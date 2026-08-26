"""Characterization tests de las 11 funciones públicas de
`app/api/v1/cart/service.py` (specs/015-caracterizacion-cart, Historia 1).

Cada test CONGELA el comportamiento actual del código, ejercitando el motor
de catálogo, las promociones y el checkout reales (sin mocks) contra SQLite
en memoria vía `cart_fixtures.py`. Incluye los dos casos de anomalía ya
documentados que esta spec NO corrige (FR-011):

  - A-17 (R16): `test_open_session_a17_r16_...`
  - A-08: `test_open_session_y_serialize_cart_a08_...`

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_cart_service -v
"""
from datetime import datetime, time, timezone
from decimal import Decimal
import unittest
from unittest import mock
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound

from app.characterization_tests import cart_fixtures
from app.api.v1.cart import service
from app.api.v1.cart.schemas import (
    CartItemIn, CartItemUpdate, CartResponse, SessionOpenResponse,
)
from app.models.cart import Cart
from app.models.customer_order import CustomerOrder
from app.models.session_participant import SessionParticipant
from app.models.table_session import TableSession


class TestCartService(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_session(self, *, table_status: str = "libre"):
        """`db` + mesa + `TableSession` activa + comensal, listos para operar
        el carrito. Devuelve `(db, table, table_session, participant)`."""
        db = cart_fixtures.new_session()
        table = cart_fixtures.make_dining_table(db, status=table_status)
        table_session = cart_fixtures.make_table_session(db, table=table)
        participant = cart_fixtures.make_participant(db, table_session=table_session)
        return db, table, table_session, participant

    def _seed_variant(self, db, **kw):
        category = cart_fixtures.make_category(db)
        product = cart_fixtures.make_product(db, category=category)
        kw.setdefault("price", Decimal("8000"))
        return cart_fixtures.make_variant(db, product=product, **kw), product, category

    def _seed_efectivo(self, db):
        """spec 025: `submit_cart` exige un método de pago activo — la
        mayoría de estos tests solo necesitan que exista uno, sin ejercitar
        ninguna regla de negocio sobre él (Principio III, actualización
        explícita citando esta spec)."""
        return cart_fixtures.make_payment_method(db, name=f"efectivo-{uuid4()}", is_cash=True)

    # ------------------------------------------------------- unique_display_label (T009)

    def test_unique_display_label(self):
        """CONGELA comportamiento actual: un nombre sin colisión se devuelve
        tal cual; con un participante existente del mismo nombre en la
        sesión, se sufija de forma determinista ("Ana" -> "Ana (2)" ->
        "Ana (3)")."""
        db = cart_fixtures.new_session()
        table = cart_fixtures.make_dining_table(db)
        ts = cart_fixtures.make_table_session(db, table=table)

        self.assertEqual(service.unique_display_label(db, ts.id, "Ana"), "Ana")

        cart_fixtures.make_participant(
            db, table_session=ts, display_name="Ana", display_label="Ana"
        )
        self.assertEqual(service.unique_display_label(db, ts.id, "Ana"), "Ana (2)")

        cart_fixtures.make_participant(
            db, table_session=ts, display_name="Ana", display_label="Ana (2)"
        )
        self.assertEqual(service.unique_display_label(db, ts.id, "Ana"), "Ana (3)")

    # ------------------------------------------------------------ open_session (T010)

    def test_open_session_camino_feliz(self):
        """CONGELA comportamiento actual: mesa activa crea `TableSession` +
        `SessionParticipant` + carrito inicial 'abierto'; `expires_at` sale
        de `_now() + SESSION_TTL_MINUTES`; la mesa pasa a 'ocupada'."""
        db = cart_fixtures.new_session()
        table = cart_fixtures.make_dining_table(db, status="libre")

        resp = service.open_session(db, 1, table, "Ana")

        self.assertIsInstance(resp, SessionOpenResponse)
        self.assertEqual(resp.display_name, "Ana")
        self.assertEqual(resp.display_label, "Ana")
        self.assertIsNotNone(resp.session_token)
        self.assertIsNotNone(resp.expires_at)
        self.assertEqual(resp.table.id, table.id)

        self.assertEqual(table.status, "ocupada")

        ts = db.get(TableSession, resp.table_session_id)
        self.assertEqual(ts.status, "active")

        cart = db.get(Cart, resp.cart_id)
        self.assertEqual(cart.status, "abierto")

    def test_open_session_segundo_comensal_se_une_a_la_misma_sesion(self):
        """CONGELA comportamiento actual: escanear el QR de una mesa ya
        ocupada une al segundo comensal a la `TableSession` en curso; no abre
        una segunda. La mesa ya estaba 'ocupada', así que no se dispara otro
        cambio de estado."""
        db = cart_fixtures.new_session()
        table = cart_fixtures.make_dining_table(db, status="libre")

        first = service.open_session(db, 1, table, "Ana")
        second = service.open_session(db, 1, table, "Ana")

        self.assertEqual(first.table_session_id, second.table_session_id)
        self.assertEqual(second.display_label, "Ana (2)")

    # ------------------------------------------------- open_session — A-17/R16 (T011)

    def test_open_session_a17_r16_doble_sesion_activa_no_controlada(self):
        """CONGELA comportamiento actual (A-17 / R16): si la mesa ya tiene
        DOS `TableSession` en `status='active'` (estado hoy alcanzable, ver
        research.md §3 — el índice único parcial no lo impide en producción
        bajo una carrera, y aquí se siembra directo para reproducirlo),
        `_get_or_create_table_session` usa `scalar_one_or_none()`: con más de
        una fila coincidente, SQLAlchemy lanza `MultipleResultsFound`, que
        `open_session` no traduce a un 4xx explícito — se propaga tal cual
        tras el rollback."""
        db = cart_fixtures.new_session()
        table = cart_fixtures.make_dining_table(db, status="libre")
        cart_fixtures.make_table_session(db, table=table, status="active")
        cart_fixtures.make_table_session(db, table=table, status="active")

        with self.assertRaises(MultipleResultsFound):
            service.open_session(db, 1, table, "Ana")

    # --------------------------------------------------- open_session/serialize — A-08 (T012)

    def test_serialize_cart_a08_zona_horaria_aplicada_tras_la_correccion(self):
        """CONGELA comportamiento corregido — A-08 (`cart/service.py:205`,
        cierre: specs/022-correccion-zona-horaria-menu-carrito): `serialize_cart`
        ahora pasa un `datetime` aware (`datetime.now(timezone.utc)`) a
        `promotions.local_now()`, que lo convierte correctamente a hora local
        del tenant (`TENANT_TIMEZONE=America/Bogota`, UTC-5) en vez de
        tratarlo como si ya lo estuviera. A las 20:00 UTC (15:00 Bogotá real,
        fuera de la ventana 20:00-21:00 local), una promoción con esa ventana
        horaria ya NO aparece vigente ni descuenta — antes de esta corrección,
        sí lo hacía (registro-de-anomalias.md, A-08)."""
        db, table, ts, participant = self._seed_session()
        variant, product, category = self._seed_variant(db)
        cart_fixtures.make_promotion(
            db, type="percent", value=Decimal("20"), status="active",
            start_time=time(20, 0), end_time=time(21, 0),
        )

        instant = datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
        with cart_fixtures.frozen_now(instant):
            resp = service.add_item(
                db, participant.id,
                CartItemIn(product_variant_id=variant.id, quantity=1),
            )

        self.assertIsNone(resp.discounted_total)

    def test_serialize_cart_dentro_de_ventana_en_hora_local_si_descuenta(self):
        """CA3 (sin regresión): a la 01:00 UTC del día siguiente (20:00
        Bogotá, dentro de la ventana 20:00-21:00 local) el carrito SÍ debe
        aplicar el descuento — mismo resultado que ya producía el caso
        correcto antes de esta corrección."""
        db, table, ts, participant = self._seed_session()
        variant, product, category = self._seed_variant(db)
        cart_fixtures.make_promotion(
            db, type="percent", value=Decimal("20"), status="active",
            start_time=time(20, 0), end_time=time(21, 0),
        )

        instant = datetime(2026, 1, 16, 1, 0, tzinfo=timezone.utc)
        with cart_fixtures.frozen_now(instant):
            resp = service.add_item(
                db, participant.id,
                CartItemIn(product_variant_id=variant.id, quantity=1),
            )

        self.assertIsNotNone(resp.discounted_total)
        self.assertLess(resp.discounted_total, resp.total)

    # ------------------------------------------------------------------ get_cart (T013)

    def test_get_cart_carrito_existente(self):
        """CONGELA comportamiento actual: `get_cart` serializa el carrito
        abierto del comensal, incluyendo su nombre (no viaja en el token de
        sesión)."""
        db, table, ts, participant = self._seed_session()
        variant, _, _ = self._seed_variant(db)
        service.add_item(db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=1))

        resp = service.get_cart(db, participant.id)

        self.assertIsInstance(resp, CartResponse)
        self.assertEqual(resp.participant_id, participant.id)
        self.assertEqual(resp.display_name, participant.display_name)
        self.assertEqual(len(resp.items), 1)

    def test_get_cart_sin_carrito_abierto_crea_uno_nuevo(self):
        """CONGELA comportamiento actual: si el comensal no tiene carrito
        'abierto' (p. ej. justo tras crearse el participante sin que
        `open_session` le haya sembrado uno), `get_cart` no responde 404: usa
        `_get_or_create_open_cart`, que crea uno vacío en el acto."""
        db = cart_fixtures.new_session()
        table = cart_fixtures.make_dining_table(db)
        ts = cart_fixtures.make_table_session(db, table=table)
        participant = cart_fixtures.make_participant(db, table_session=ts)

        resp = service.get_cart(db, participant.id)

        self.assertEqual(resp.status, "abierto")
        self.assertEqual(resp.items, [])

    # -------------------------------------------------------------------- add_item (T014)

    def test_add_item_variante_con_opciones(self):
        """CONGELA comportamiento actual: variante activa + opciones válidas
        delegan en `compute_line_price`/`load_valid_options`/
        `check_availability` reales; el precio de línea, las opciones
        guardadas y el `CartResponse` coinciden con lo que produce el código
        hoy."""
        db, table, ts, participant = self._seed_session()
        variant, product, category = self._seed_variant(db, price=Decimal("8000"))
        group = cart_fixtures.make_option_group(db, min_select=1, max_select=1)
        option = cart_fixtures.make_option(db, group=group, extra_price=Decimal("500"))
        cart_fixtures.link_variant_group(db, variant, group, min_select=1, max_select=1)

        resp = service.add_item(
            db, participant.id,
            CartItemIn(product_variant_id=variant.id, quantity=2, option_ids=[option.id]),
        )

        self.assertEqual(len(resp.items), 1)
        item = resp.items[0]
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.unit_price, Decimal("8500"))
        self.assertEqual(item.line_total, Decimal("17000"))
        self.assertEqual([o.option_id for o in item.options], [option.id])

    def test_add_item_variante_inactiva_422(self):
        """CONGELA comportamiento actual: agregar una variante inactiva
        responde 422 antes de tocar el motor de precio/disponibilidad."""
        db, table, ts, participant = self._seed_session()
        variant, _, _ = self._seed_variant(db, active=False)

        with self.assertRaises(HTTPException) as ctx:
            service.add_item(
                db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=1)
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_add_item_combo(self):
        """CONGELA comportamiento actual: seleccionar un combo (`combo_id`)
        delega en `promotions.expand_combo` y expande una `CartItem` por
        componente, a precio normal (sin opciones), con `combo_id` marcado en
        cada línea."""
        db, table, ts, participant = self._seed_session()
        variant1, product, category = self._seed_variant(db, price=Decimal("6000"))
        variant2 = cart_fixtures.make_variant(db, product=product, price=Decimal("7000"))
        combo = cart_fixtures.make_promotion(db, type="combo", value=Decimal("11000"), status="active")
        cart_fixtures.make_combo_item(db, combo, variant1, quantity=1)
        cart_fixtures.make_combo_item(db, combo, variant2, quantity=1)

        resp = service.add_item(db, participant.id, CartItemIn(combo_id=combo.id, quantity=1))

        combo_items = [it for it in resp.items if it.combo_id == combo.id]
        self.assertEqual(len(combo_items), 2)
        self.assertEqual(
            sorted(it.unit_price for it in combo_items),
            [Decimal("6000"), Decimal("7000")],
        )

    # ----------------------------------------------------------------- update_item (T015)

    def test_update_item_cambia_cantidad_y_recalcula_precio(self):
        """CONGELA comportamiento actual: `update_item` cambia la cantidad de
        una línea existente y recalcula `unit_price`/`line_total` a partir
        del motor de precio real."""
        db, table, ts, participant = self._seed_session()
        variant, _, _ = self._seed_variant(db, price=Decimal("5000"))
        added = service.add_item(
            db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=1)
        )
        item_id = added.items[0].id

        resp = service.update_item(db, participant.id, item_id, CartItemUpdate(quantity=3))

        self.assertEqual(resp.items[0].quantity, 3)
        self.assertEqual(resp.items[0].line_total, Decimal("15000"))

    def test_update_item_no_existente_404(self):
        """CONGELA comportamiento actual: editar una línea que no pertenece
        al carrito abierto del comensal responde 404."""
        db, table, ts, participant = self._seed_session()
        cart_fixtures.make_cart(db, participant=participant)
        from uuid import uuid4
        with self.assertRaises(HTTPException) as ctx:
            service.update_item(db, participant.id, uuid4(), CartItemUpdate(quantity=2))
        self.assertEqual(ctx.exception.status_code, 404)

    # ----------------------------------------------------------------- remove_item (T016)

    def test_remove_item_ultima_linea_deja_carrito_vacio(self):
        """CONGELA comportamiento actual: `remove_item` elimina la línea
        indicada; al quitar la última, el `CartResponse` queda con
        `items == []` (el carrito sigue 'abierto', no se borra)."""
        db, table, ts, participant = self._seed_session()
        variant, _, _ = self._seed_variant(db)
        added = service.add_item(
            db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=1)
        )
        item_id = added.items[0].id

        resp = service.remove_item(db, participant.id, item_id)

        self.assertEqual(resp.items, [])
        self.assertEqual(resp.status, "abierto")

    # ------------------------------------------------- serialize_cart / discounted_total (T017)

    def test_serialize_cart_discounted_total_sin_promocion(self):
        """CONGELA comportamiento actual (FR-007): sin ninguna promoción
        automática activa, `discounted_total` es `None` (no cero)."""
        db, table, ts, participant = self._seed_session()
        variant, _, _ = self._seed_variant(db)

        resp = service.add_item(
            db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=1)
        )

        self.assertIsNone(resp.discounted_total)

    def test_serialize_cart_discounted_total_con_promocion_activa(self):
        """CONGELA comportamiento actual: con una promoción `percent` activa
        cuyo alcance incluye la categoría de la línea, `discounted_total`
        queda por debajo de `total` y la línea trae su
        `discounted_unit_price`/`discounted_line_total`."""
        db, table, ts, participant = self._seed_session()
        variant, product, category = self._seed_variant(db, price=Decimal("10000"))
        promo = cart_fixtures.make_promotion(db, type="percent", value=Decimal("10"), status="active")
        cart_fixtures.make_promotion_target(db, promo, category_id=category.id)

        resp = service.add_item(
            db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=1)
        )

        self.assertIsNotNone(resp.discounted_total)
        self.assertEqual(resp.discounted_total, Decimal("9000.00"))
        self.assertEqual(resp.items[0].discounted_line_total, Decimal("9000.00"))

    def test_serialize_cart_combo_no_recibe_descuento_adicional(self):
        """CONGELA comportamiento actual: las líneas de combo (`combo_id`
        distinto de `None`) no reciben además un descuento percent/fixed,
        aunque una promoción automática activa alcance su categoría — el
        ahorro del combo ya se calcula aparte, al cobrar
        (`combo_discount_for_lines`)."""
        db, table, ts, participant = self._seed_session()
        variant1, product, category = self._seed_variant(db, price=Decimal("6000"))
        variant2 = cart_fixtures.make_variant(db, product=product, price=Decimal("7000"))
        combo = cart_fixtures.make_promotion(db, type="combo", value=Decimal("11000"), status="active")
        cart_fixtures.make_combo_item(db, combo, variant1, quantity=1)
        cart_fixtures.make_combo_item(db, combo, variant2, quantity=1)
        percent_promo = cart_fixtures.make_promotion(
            db, type="percent", value=Decimal("50"), status="active"
        )
        cart_fixtures.make_promotion_target(db, percent_promo, category_id=category.id)

        resp = service.add_item(db, participant.id, CartItemIn(combo_id=combo.id, quantity=1))

        combo_items = [it for it in resp.items if it.combo_id == combo.id]
        self.assertEqual(len(combo_items), 2)
        for it in combo_items:
            self.assertIsNone(it.discounted_unit_price)
            self.assertIsNone(it.discounted_line_total)

    # --------------------------------------------------------------- list_my_orders (T018)

    def test_list_my_orders_mas_reciente_primero(self):
        """CONGELA comportamiento actual: `list_my_orders` devuelve los
        pedidos del comensal ordenados por `created_at` descendente (más
        reciente primero).

        Actualizado por spec 024-pagos-ordenes-mesa (FR-005/FR-006, Principio
        III): `order1` se marca `pagada` antes de enviar `order2` porque,
        desde esta spec, un comensal no puede tener dos órdenes activas a la
        vez — el propio orden de creación que este test verifica no cambia,
        solo se agrega el paso que finaliza la primera para poder enviar la
        segunda. Actualizado de nuevo por spec 025-revision-pago-antes-envio:
        `submit_cart` exige `payment_method_id` (el pedido nace con su
        primer intento de pago adjunto) — el resto de la suite sigue en
        verde."""
        db, table, ts, participant = self._seed_session()
        variant, _, _ = self._seed_variant(db)
        efectivo = self._seed_efectivo(db)

        service.add_item(db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=1))
        order1 = service.submit_cart(db, participant, efectivo.id)
        order1.created_at = datetime(2020, 1, 1)
        order1.status = "pagada"
        db.commit()

        service.add_item(db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=1))
        order2 = service.submit_cart(db, participant, efectivo.id)
        order2.created_at = datetime(2020, 1, 2)
        db.commit()

        orders = service.list_my_orders(db, participant.id)

        self.assertEqual([o.id for o in orders], [order2.id, order1.id])

    # ------------------------------------------------------------- cancel_my_order (T019)

    def test_cancel_my_order_recibida_se_cancela(self):
        """CONGELA comportamiento actual: un pedido en 'recibida' (sin
        ítems en cocina, porque aún no se confirmó) se cancela sin
        restricción.

        Actualizado por spec 025-revision-pago-antes-envio: `submit_cart`
        exige `payment_method_id`."""
        db, table, ts, participant = self._seed_session()
        variant, _, _ = self._seed_variant(db)
        efectivo = self._seed_efectivo(db)
        service.add_item(db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=1))
        order = service.submit_cart(db, participant, efectivo.id)
        self.assertEqual(order.status, "recibida")

        cancelled = service.cancel_my_order(db, participant, order.id, "Me equivoqué de sabor")

        self.assertEqual(cancelled.status, "cancelada")

    def test_cancel_my_order_en_preparacion_409(self):
        """CONGELA comportamiento actual: un pedido cuyos ítems ya están
        'en_preparacion' (staff ya confirmó y cocina ya empezó) responde 409
        al intento de cancelación del propio comensal, tal como documenta el
        docstring de `cancel_my_order`.

        Actualizado por spec 025-revision-pago-antes-envio: `submit_cart`
        exige `payment_method_id`."""
        db, table, ts, participant = self._seed_session()
        variant, _, _ = self._seed_variant(db)
        efectivo = self._seed_efectivo(db)
        service.add_item(db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=1))
        order = service.submit_cart(db, participant, efectivo.id)
        order.status = "abierta"
        order.items[0].estado_cocina = "en_preparacion"
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            service.cancel_my_order(db, participant, order.id, "Ya no quiero")
        self.assertEqual(ctx.exception.status_code, 409)

    # --------------------------------------------------------------- leave_session (T020)

    def test_leave_session_cierra_participante_abandona_carrito_y_libera_mesa(self):
        """CONGELA comportamiento actual: `leave_session` cierra al
        comensal (`status='closed'`), abandona su carrito abierto
        (`status='abandonado'`) y, al ser el último de la mesa sin nada que
        cobrar, libera la mesa en el acto (`try_release_if_empty`)."""
        db = cart_fixtures.new_session()
        table = cart_fixtures.make_dining_table(db, status="libre")
        resp = service.open_session(db, 1, table, "Ana")
        participant = db.get(SessionParticipant, resp.participant_id)

        service.leave_session(db, participant)

        self.assertEqual(participant.status, "closed")
        cart = db.get(Cart, resp.cart_id)
        self.assertEqual(cart.status, "abandonado")
        self.assertEqual(table.status, "libre")

    # ----------------------------------------------------------------- submit_cart (T021)

    def test_submit_cart_elimina_carrito_y_abre_uno_nuevo_tras_pedido(self):
        """CONGELA comportamiento corregido — spec 038 (FR-003/FR-004): un
        carrito con ítems se confirma en una `CustomerOrder` 'recibida' y el
        carrito del participante se ELIMINA físicamente (no queda ninguna
        fila, ni 'confirmado' ni de ningún otro status) en la misma
        transacción del pedido. Antes de esta spec la fila quedaba
        `status='confirmado'`, huérfana para siempre (research.md Decisión
        9) — renombrado desde
        `test_submit_cart_confirma_pedido_y_abre_carrito_nuevo`.

        Actualizado por spec 025-revision-pago-antes-envio: `submit_cart`
        exige `payment_method_id` — el pedido nace con su primer intento de
        pago adjunto (`current_payment_attempt`)."""
        db, table, ts, participant = self._seed_session()
        variant, _, _ = self._seed_variant(db, price=Decimal("4000"))
        efectivo = self._seed_efectivo(db)
        service.add_item(db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=2))
        old_cart = db.execute(
            select(Cart).where(Cart.participant_id == participant.id, Cart.status == "abierto")
        ).scalar_one()

        order = service.submit_cart(db, participant, efectivo.id)

        self.assertIsInstance(order, CustomerOrder)
        self.assertEqual(order.status, "recibida")
        self.assertEqual(len(order.items), 1)
        self.assertEqual(order.items[0].quantity, 2)
        self.assertEqual(order.current_payment_attempt.payment_method_id, efectivo.id)
        self.assertEqual(order.current_payment_attempt.status, "pendiente")

        # spec 038, FR-003/FR-004: la fila y sus CartItem/CartItemOption ya
        # no existen — borrado físico, no archivado.
        self.assertIsNone(db.get(Cart, old_cart.id))

        # `submit_cart` no abre el carrito siguiente por sí solo: lo hace
        # `_get_or_create_open_cart` en la próxima operación del comensal
        # (aquí, `get_cart`) — posible sin violar el índice único parcial
        # porque el fixture lo removió (research.md §3).
        cart_resp = service.get_cart(db, participant.id)
        new_cart = db.execute(
            select(Cart).where(Cart.participant_id == participant.id, Cart.status == "abierto")
        ).scalar_one()
        self.assertNotEqual(new_cart.id, old_cart.id)
        self.assertEqual(cart_resp.items, [])

        # spec 038, US3 (Acceptance Scenario 1): agregar un ítem nuevo tras
        # confirmar no arrastra ninguna línea de la ronda ya confirmada — el
        # carrito de la segunda ronda contiene únicamente lo que se agrega
        # ahora.
        variant2, _, _ = self._seed_variant(db, price=Decimal("2500"))
        second_round = service.add_item(
            db, participant.id, CartItemIn(product_variant_id=variant2.id, quantity=1)
        )
        self.assertEqual(len(second_round.items), 1)
        self.assertEqual(second_round.items[0].product_variant_id, variant2.id)

    def test_submit_cart_snapshot_de_descuento_coincide_con_el_carrito(self):
        """spec 038, US1 (Acceptance Scenario 3 / CA-6, SC-005): el snapshot
        de descuento persistido en los `OrderItem` coincide exactamente con
        lo que `serialize_cart`/`GET /cart` mostraba justo antes de
        confirmar — la línea con promoción activa trae su
        `discounted_unit_price`/`discounted_line_total`, la otra queda en
        `None` (mismo motor, función compartida, research.md Decisión 4)."""
        db, table, ts, participant = self._seed_session()
        variant1, product, category = self._seed_variant(db, price=Decimal("10000"))
        variant2, _, _ = self._seed_variant(db, price=Decimal("5000"))
        promo = cart_fixtures.make_promotion(db, type="percent", value=Decimal("10"), status="active")
        cart_fixtures.make_promotion_target(db, promo, category_id=category.id)
        efectivo = self._seed_efectivo(db)

        service.add_item(db, participant.id, CartItemIn(product_variant_id=variant1.id, quantity=1))
        service.add_item(db, participant.id, CartItemIn(product_variant_id=variant2.id, quantity=1))

        order = service.submit_cart(db, participant, efectivo.id)

        self.assertEqual(len(order.items), 2)
        with_promo = next(it for it in order.items if it.product_variant_id == variant1.id)
        without_promo = next(it for it in order.items if it.product_variant_id == variant2.id)
        self.assertEqual(with_promo.discounted_unit_price, Decimal("9000.00"))
        self.assertEqual(with_promo.discounted_line_total, Decimal("9000.00"))
        self.assertIsNone(without_promo.discounted_unit_price)
        self.assertIsNone(without_promo.discounted_line_total)

    def test_submit_cart_fallo_en_transaccion_no_toca_el_carrito(self):
        """spec 038, US1 (Acceptance Scenario 4 / CA-8, FR-004): si la
        creación del pedido falla dentro de la transacción (aquí, forzando
        un error al resolver el snapshot de descuento — FR-005/research.md
        Decisión 5), el `Cart`/`CartItem[]` del participante siguen
        existiendo intactos (mismo `id`, mismas líneas) y no se creó ningún
        `CustomerOrder` — el `rollback()` deshace tanto el pedido a medias
        como el borrado del carrito."""
        db, table, ts, participant = self._seed_session()
        variant, _, _ = self._seed_variant(db, price=Decimal("4000"))
        efectivo = self._seed_efectivo(db)
        service.add_item(db, participant.id, CartItemIn(product_variant_id=variant.id, quantity=2))
        cart = db.execute(
            select(Cart).where(Cart.participant_id == participant.id, Cart.status == "abierto")
        ).scalar_one()
        cart_id = cart.id
        item_ids = {it.id for it in cart.items}

        with mock.patch(
            "app.api.v1.cart.service.promotions.active_discount_promotions",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                service.submit_cart(db, participant, efectivo.id)

        persisted = db.get(Cart, cart_id)
        self.assertIsNotNone(persisted)
        self.assertEqual({it.id for it in persisted.items}, item_ids)
        orders = db.execute(
            select(CustomerOrder).where(CustomerOrder.participant_id == participant.id)
        ).scalars().all()
        self.assertEqual(orders, [])

    def test_submit_cart_segunda_ronda_crea_orden_independiente(self):
        """spec 038, US3 (Acceptance Scenario 2): confirmar el carrito de la
        segunda ronda crea un segundo `CustomerOrder` independiente del
        primero, con ambos coexistiendo con sus respectivas líneas. Usa dos
        participantes para no chocar con el 409 de "orden activa" de FR-008
        (esa garantía es por participante, no por mesa)."""
        db, table, ts, participant1 = self._seed_session()
        participant2 = cart_fixtures.make_participant(db, table_session=ts)
        variant1, _, _ = self._seed_variant(db, price=Decimal("4000"))
        variant2, _, _ = self._seed_variant(db, price=Decimal("3000"))
        efectivo = self._seed_efectivo(db)

        service.add_item(db, participant1.id, CartItemIn(product_variant_id=variant1.id, quantity=1))
        order1 = service.submit_cart(db, participant1, efectivo.id)

        service.add_item(db, participant2.id, CartItemIn(product_variant_id=variant2.id, quantity=1))
        order2 = service.submit_cart(db, participant2, efectivo.id)

        self.assertNotEqual(order1.id, order2.id)
        orders = db.execute(
            select(CustomerOrder).where(CustomerOrder.table_session_id == ts.id)
        ).scalars().all()
        self.assertEqual({o.id for o in orders}, {order1.id, order2.id})
        self.assertEqual(len(order1.items), 1)
        self.assertEqual(len(order2.items), 1)

    def test_submit_cart_no_afecta_el_carrito_de_otro_comensal_de_la_mesa(self):
        """spec 038, US4 (Acceptance Scenario 1 / CA-5, FR-006): confirmar
        el pedido de un comensal no toca el carrito de otro comensal de la
        misma mesa — el borrado es estrictamente por `participant_id`."""
        db, table, ts, ana = self._seed_session()
        beto = cart_fixtures.make_participant(db, table_session=ts)
        variant_ana, _, _ = self._seed_variant(db, price=Decimal("4000"))
        variant_beto, _, _ = self._seed_variant(db, price=Decimal("3000"))
        efectivo = self._seed_efectivo(db)

        service.add_item(db, ana.id, CartItemIn(product_variant_id=variant_ana.id, quantity=1))
        service.add_item(db, beto.id, CartItemIn(product_variant_id=variant_beto.id, quantity=2))
        beto_cart = db.execute(
            select(Cart).where(Cart.participant_id == beto.id, Cart.status == "abierto")
        ).scalar_one()
        beto_cart_id = beto_cart.id
        beto_item_ids = {it.id for it in beto_cart.items}

        service.submit_cart(db, ana, efectivo.id)

        persisted = db.get(Cart, beto_cart_id)
        self.assertIsNotNone(persisted)
        self.assertEqual({it.id for it in persisted.items}, beto_item_ids)
        self.assertEqual(len(persisted.items), 1)
        self.assertEqual(persisted.items[0].quantity, 2)

    def test_submit_cart_vacio_409(self):
        """CONGELA comportamiento actual: enviar un carrito sin ítems
        responde 409 ("El carrito está vacío") — se evalúa antes que
        cualquier validación de pago (contracts/submit-cart-with-payment.md,
        spec 025)."""
        db, table, ts, participant = self._seed_session()
        cart_fixtures.make_cart(db, participant=participant)

        with self.assertRaises(HTTPException) as ctx:
            service.submit_cart(db, participant, uuid4())
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
