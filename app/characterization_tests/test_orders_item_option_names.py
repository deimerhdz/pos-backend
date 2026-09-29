"""Nombre y grupo de los adicionales de un ítem de pedido — spec 087, FR-015 (A-89).

No son characterization tests en sentido estricto (no había comportamiento
previo que congelar: `name`/`group_name` son campos nuevos de solo lectura en
`OrderItemOptionResponse`): verifican que el backend resuelve el nombre de cada
opción guardada por JOIN en lectura, sin depender del menú vigente ni de
recargas por opción (sin N+1) — `research.md` D12, `contracts/api-changes.md` §7.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_orders_item_option_names -v
"""
import unittest

from sqlalchemy import event

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.orders import service
from app.api.v1.orders.schemas import OrderResponse
from app.models.order_item import OrderItemOption


class TestOrdersItemOptionNames(unittest.TestCase):
    # ------------------------------------------------------------- Helpers

    def _seed_pedido(self, db, *, n_opciones: int = 2, producto_activo: bool = True):
        """Pedido con un ítem y `n_opciones` opciones de un mismo grupo (cantidades
        1, 2, 1, 2, …). Devuelve `(order, grupo, opciones)`."""
        category = fx.make_category(db)
        product = fx.make_product(
            db, category=category, active=producto_activo, available=producto_activo
        )
        variant = fx.make_variant(db, product=product)
        grupo = fx.make_option_group(db, name="Adicionales")
        fx.link_variant_group(db, variant, grupo)
        opciones = [
            fx.make_option(db, grupo, name=f"Adicional {i + 1}", active=producto_activo)
            for i in range(n_opciones)
        ]
        session = fx.make_table_session(db)
        order = fx.make_customer_order(db, session)
        item = fx.make_order_item(db, order, variant)
        for i, opcion in enumerate(opciones):
            db.add(
                OrderItemOption(
                    order_item_id=item.id, option_id=opcion.id, quantity=(i % 2) + 1
                )
            )
        db.commit()
        db.expire_all()
        return order, grupo, opciones

    # ------------------------------------------------------------ Escenarios

    def test_opciones_del_item_incluyen_name_y_group_name(self):
        db = fx.new_session()
        order, grupo, opciones = self._seed_pedido(db, n_opciones=2)

        (cargado,) = [o for o in service.list_orders(db) if o.id == order.id]
        respuesta = OrderResponse.model_validate(cargado)

        por_id = {o.option_id: o for o in respuesta.items[0].options}
        self.assertEqual(por_id[opciones[0].id].name, "Adicional 1")
        self.assertEqual(por_id[opciones[0].id].quantity, 1)
        self.assertEqual(por_id[opciones[1].id].name, "Adicional 2")
        self.assertEqual(por_id[opciones[1].id].quantity, 2)
        self.assertEqual(por_id[opciones[0].id].group_name, "Adicionales")
        self.assertEqual(por_id[opciones[1].id].group_name, "Adicionales")

    def test_nombre_se_resuelve_aunque_el_producto_ya_no_este_disponible(self):
        """Acceptance Scenario 3: un adicional guardado sigue viéndose aunque su
        producto/opción se haya desactivado después."""
        db = fx.new_session()
        order, grupo, opciones = self._seed_pedido(db, n_opciones=1, producto_activo=False)

        (cargado,) = [o for o in service.list_orders(db) if o.id == order.id]
        opcion = OrderResponse.model_validate(cargado).items[0].options[0]

        self.assertEqual(opcion.name, "Adicional 1")
        self.assertEqual(opcion.group_name, "Adicionales")

    def test_agregar_opciones_no_aumenta_el_numero_de_sentencias(self):
        """Sin N+1 (contracts §7): el nombre viaja en el mismo SELECT de las
        opciones, así que más opciones no significa más sentencias."""

        def sentencias_para(n_opciones: int) -> int:
            db = fx.new_session()
            self._seed_pedido(db, n_opciones=n_opciones)
            contador = {"n": 0}

            def _contar(conn, cursor, statement, parameters, context, executemany):
                contador["n"] += 1

            engine = db.get_bind().engine
            event.listen(engine, "before_cursor_execute", _contar)
            try:
                for pedido in service.list_orders(db):
                    OrderResponse.model_validate(pedido)
            finally:
                event.remove(engine, "before_cursor_execute", _contar)
            return contador["n"]

        self.assertEqual(sentencias_para(1), sentencias_para(6))


if __name__ == "__main__":
    unittest.main()
