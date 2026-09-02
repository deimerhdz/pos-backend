"""Tests de la nueva funcionalidad (spec 065): selección por cantidad en grupos de
opciones ("cantidad" vs "conteo"). A diferencia del resto de
`characterization_tests/`, estos no "congelan" comportamiento heredado -- verifican
comportamiento NUEVO definido en `specs/065-opciones-por-cantidad/spec.md`
(FR-002 a FR-011).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_catalog_option_groups_quantity -v
"""
from decimal import Decimal
from uuid import uuid4
import unittest

from fastapi import HTTPException
from sqlalchemy import select

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.catalog.router import update_option_group
from app.api.v1.catalog.schemas import OptionGroupUpdate, OptionSelectionIn
from app.catalog_engine import (
    check_availability,
    compute_line_price,
    load_valid_options,
    plan_line_consumption,
    required_consumption,
    validate_option_selection,
)
from app.api.v1.orders import service as orders_service
from app.api.v1.orders.schemas import OrderChannel, OrderCreate, OrderItemIn
from app.api.v1.sales.consumption import deduct_sale
from app.models.inventory_movement import InventoryMovement
from app.models.sale import Sale, SaleItem

PRECIO = Decimal("15000")


def _grupo_cantidad(db, **kw):
    """Grupo "cantidad" listo para vender: `min_select=0`/`max_select` arbitrarios
    (se ignoran en este modo) para no depender de su valor por defecto."""
    kw.setdefault("min_select", 0)
    kw.setdefault("max_select", 1)
    kw.setdefault("selection_mode", "cantidad")
    return fx.make_option_group(db, **kw)


class LoadValidOptionsQuantityTests(unittest.TestCase):
    """FR-002: `load_valid_options` produce `ChosenOption` con la cantidad de cada
    `OptionSelectionIn`."""

    def setUp(self):
        self.db = fx.new_session()

    def test_quantity_por_defecto_es_1(self):
        group = _grupo_cantidad(self.db)
        opt = fx.make_option(self.db, group=group)
        chosen = load_valid_options(self.db, [OptionSelectionIn(option_id=opt.id)])
        self.assertEqual(chosen[0].quantity, 1)

    def test_quantity_explicita_se_propaga(self):
        group = _grupo_cantidad(self.db)
        opt = fx.make_option(self.db, group=group)
        chosen = load_valid_options(
            self.db, [OptionSelectionIn(option_id=opt.id, quantity=3)]
        )
        self.assertEqual(chosen[0].quantity, 3)

    def test_option_id_repetido_lanza_422(self):
        group = _grupo_cantidad(self.db)
        opt = fx.make_option(self.db, group=group)
        with self.assertRaises(HTTPException) as ctx:
            load_valid_options(
                self.db,
                [OptionSelectionIn(option_id=opt.id, quantity=1),
                 OptionSelectionIn(option_id=opt.id, quantity=2)],
            )
        self.assertEqual(ctx.exception.status_code, 422)


class ValidateOptionSelectionQuantityTests(unittest.TestCase):
    """FR-003: un grupo "cantidad" nunca exige mínimo; una opción de un grupo
    "conteo" con `quantity>1` se rechaza (research.md Decisión 3)."""

    def setUp(self):
        self.db = fx.new_session()

    def test_grupo_conteo_con_quantity_2_rechaza_422(self):
        variant = fx.make_variant(self.db, price=PRECIO)
        group = fx.make_option_group(self.db, min_select=0, max_select=1, selection_mode="conteo")
        fx.link_variant_group(self.db, variant, group, min_select=0, max_select=1)
        opt = fx.make_option(self.db, group=group)

        chosen = load_valid_options(
            self.db, [OptionSelectionIn(option_id=opt.id, quantity=2)]
        )
        with self.assertRaises(HTTPException) as ctx:
            validate_option_selection(self.db, variant, chosen)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("no admite más de una unidad", str(ctx.exception.detail))

    def test_grupo_cantidad_sin_ninguna_seleccion_no_bloquea(self):
        """Regresión: un grupo "cantidad" nunca es obligatorio, incluso si el
        `VariantOptionGroup.min_select` heredado quedó en un valor > 0 (nunca debería
        pasar desde el formulario, pero el motor no debe asumirlo)."""
        variant = fx.make_variant(self.db, price=PRECIO)
        group = _grupo_cantidad(self.db)
        fx.link_variant_group(self.db, variant, group, min_select=1, max_select=1)
        fx.make_option(self.db, group=group)

        validate_option_selection(self.db, variant, [])  # no lanza

    def test_grupo_cantidad_con_varias_opciones_y_cantidades_no_bloquea(self):
        variant = fx.make_variant(self.db, price=PRECIO)
        group = _grupo_cantidad(self.db)
        fx.link_variant_group(self.db, variant, group)
        bobombun = fx.make_option(self.db, group=group, extra_price=Decimal("1000"))
        gomitas = fx.make_option(self.db, group=group, extra_price=Decimal("800"))

        chosen = load_valid_options(
            self.db,
            [OptionSelectionIn(option_id=bobombun.id, quantity=2),
             OptionSelectionIn(option_id=gomitas.id, quantity=1)],
        )
        validate_option_selection(self.db, variant, chosen)  # no lanza


class PriceAndConsumptionQuantityTests(unittest.TestCase):
    """FR-004/FR-005/FR-006/FR-007: precio y consumo multiplican por la cantidad
    elegida; "incluido" sigue en $0 sin importar cuántas se pidan."""

    def setUp(self):
        self.db = fx.new_session()

    def test_precio_de_linea_multiplica_extra_price_por_cantidad(self):
        variant = fx.make_variant(self.db, price=PRECIO)
        group = _grupo_cantidad(self.db)
        fx.link_variant_group(self.db, variant, group)
        bobombun = fx.make_option(self.db, group=group, extra_price=Decimal("1000"))
        gomitas = fx.make_option(self.db, group=group, extra_price=Decimal("800"))

        chosen = load_valid_options(
            self.db,
            [OptionSelectionIn(option_id=bobombun.id, quantity=2),
             OptionSelectionIn(option_id=gomitas.id, quantity=1)],
            variant=variant,
        )
        # 15000 (variante) + 2*1000 (bobombún) + 1*800 (gomitas) = 17800
        self.assertEqual(compute_line_price(variant, chosen), Decimal("17800"))

    def test_grupo_cantidad_incluido_no_suma_recargo_sin_importar_cantidad(self):
        variant = fx.make_variant(self.db, price=PRECIO)
        group = _grupo_cantidad(self.db, pricing_type="incluido")
        fx.link_variant_group(self.db, variant, group)
        opt = fx.make_option(self.db, group=group, extra_price=Decimal("0"))

        chosen = load_valid_options(
            self.db, [OptionSelectionIn(option_id=opt.id, quantity=5)], variant=variant
        )
        self.assertEqual(compute_line_price(variant, chosen), PRECIO)

    def test_plan_line_consumption_multiplica_por_cantidad_elegida(self):
        variant = fx.make_variant(self.db, price=PRECIO)
        group = _grupo_cantidad(self.db)
        fx.link_variant_group(self.db, variant, group, quantity_per_option=Decimal("0"))
        insumo = fx.make_inventory_item(self.db)
        opt = fx.make_option(
            self.db, group=group, inventory_item_id=insumo.id, item_quantity=Decimal("10")
        )

        chosen = load_valid_options(
            self.db, [OptionSelectionIn(option_id=opt.id, quantity=3)], variant=variant
        )
        lines = plan_line_consumption(self.db, variant.id, 2, chosen)
        # per_unit(10) * cantidad_vendida(2) * cantidad_opcion(3) = 60
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].quantity, Decimal("60"))

    def test_check_availability_recibe_el_total_ya_multiplicado(self):
        variant = fx.make_variant(self.db, price=PRECIO)
        group = _grupo_cantidad(self.db)
        fx.link_variant_group(self.db, variant, group, quantity_per_option=Decimal("0"))
        insumo = fx.make_inventory_item(self.db, current_stock=Decimal("15"))
        opt = fx.make_option(
            self.db, group=group, inventory_item_id=insumo.id, item_quantity=Decimal("10")
        )

        chosen = load_valid_options(
            self.db, [OptionSelectionIn(option_id=opt.id, quantity=2)], variant=variant
        )
        # requerido = 10*1*2 = 20 > 15 disponibles -> 409
        req = required_consumption(self.db, variant.id, 1, chosen)
        with self.assertRaises(HTTPException) as ctx:
            check_availability(self.db, req)
        self.assertEqual(ctx.exception.status_code, 409)


class QuantityCapsTests(unittest.TestCase):
    """FR-008/FR-009 (US4): topes opcionales por opción y por total del grupo."""

    def setUp(self):
        self.db = fx.new_session()

    def test_excede_max_quantity_per_option_rechaza_422(self):
        variant = fx.make_variant(self.db, price=PRECIO)
        group = _grupo_cantidad(self.db, max_quantity_per_option=3)
        fx.link_variant_group(self.db, variant, group)
        opt = fx.make_option(self.db, group=group)

        chosen = load_valid_options(
            self.db, [OptionSelectionIn(option_id=opt.id, quantity=4)]
        )
        with self.assertRaises(HTTPException) as ctx:
            validate_option_selection(self.db, variant, chosen)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("como máximo 3", str(ctx.exception.detail))

    def test_excede_max_total_quantity_rechaza_aunque_ninguna_individual_llegue_a_su_tope(self):
        variant = fx.make_variant(self.db, price=PRECIO)
        group = _grupo_cantidad(self.db, max_quantity_per_option=3, max_total_quantity=5)
        fx.link_variant_group(self.db, variant, group)
        bobombun = fx.make_option(self.db, group=group)
        gomitas = fx.make_option(self.db, group=group)

        chosen = load_valid_options(
            self.db,
            [OptionSelectionIn(option_id=bobombun.id, quantity=3),
             OptionSelectionIn(option_id=gomitas.id, quantity=3)],
        )
        with self.assertRaises(HTTPException) as ctx:
            validate_option_selection(self.db, variant, chosen)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("como máximo 5", str(ctx.exception.detail))

    def test_sin_topes_configurados_no_hay_limite_propio(self):
        variant = fx.make_variant(self.db, price=PRECIO)
        group = _grupo_cantidad(self.db)  # sin topes
        fx.link_variant_group(self.db, variant, group)
        opt = fx.make_option(self.db, group=group)

        chosen = load_valid_options(
            self.db, [OptionSelectionIn(option_id=opt.id, quantity=999)]
        )
        validate_option_selection(self.db, variant, chosen)  # no lanza


class EndToEndQuantityConsumptionTests(unittest.TestCase):
    """Hallazgo de `/speckit-analyze`: al menos un caso de punta a punta por cada
    camino real de venta con un grupo "cantidad" -- confirma que T032-T036 quedan
    correctamente encadenados, no solo `plan_line_consumption` de forma aislada."""

    def _seed(self, db):
        variant = fx.make_variant(db, price=PRECIO)
        group = _grupo_cantidad(db)
        fx.link_variant_group(db, variant, group, quantity_per_option=Decimal("0"))
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        opt = fx.make_option(
            db, group=group, extra_price=Decimal("1000"),
            inventory_item_id=insumo.id, item_quantity=Decimal("10"),
        )
        return variant, insumo, opt

    def test_confirmar_una_orden_completa_descuenta_el_total_multiplicado(self):
        db = fx.new_session()
        variant, insumo, opt = self._seed(db)
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.POS,
            items=[OrderItemIn(
                product_variant_id=variant.id, quantity=2,
                options=[OptionSelectionIn(option_id=opt.id, quantity=3)],
            )],
        )
        order = orders_service.create_order(db, data, uuid4())

        self.assertEqual(order.status, "abierta")
        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 1)
        # per_unit(10) * cantidad_linea(2) * cantidad_opcion(3) = 60
        self.assertEqual(Decimal(movimientos[0].quantity), Decimal("60"))
        db.refresh(insumo)
        self.assertEqual(Decimal(insumo.current_stock), Decimal("940"))

    def test_cobrar_una_venta_de_mostrador_completa_descuenta_el_total_multiplicado(self):
        """Ejercita `deduct_sale` (`sales/consumption.py`) sobre un `SaleItem` ya
        persistido con su snapshot `options` (mismo shape que arma `service.checkout`
        vía `load_valid_options`/`compute_line_price`, T021) -- no se invoca
        `sales.service.checkout` directamente porque tiene un bug preexistente y no
        relacionado con esta spec (`checkout.auto_discount`, línea 255: el nombre
        `checkout` del módulo importado queda sombreado por la propia función
        `def checkout(...)` del archivo, un `AttributeError` en cualquier venta de
        mostrador, en cualquier rama; reportado aparte, no es parte de spec 065)."""
        db = fx.new_session()
        variant, insumo, opt = self._seed(db)
        shift = fx.make_cash_shift(db)
        db.commit()

        sale = Sale(
            cash_shift_id=shift.id, user_id=uuid4(), user_name="Cajero de prueba",
            subtotal=PRECIO * 2 + Decimal("1000") * 3 * 2,
            total=PRECIO * 2 + Decimal("1000") * 3 * 2, status="paid",
        )
        db.add(sale)
        db.flush()
        db.add(SaleItem(
            sale_id=sale.id, product_variant_id=variant.id,
            description="Producto de prueba", quantity=2,
            unit_price=PRECIO + Decimal("1000") * 3,
            line_total=(PRECIO + Decimal("1000") * 3) * 2,
            options=[{
                "option_id": str(opt.id), "name": opt.name,
                "extra_price": str(opt.extra_price), "quantity": 3,
            }],
        ))
        db.commit()

        deduct_sale(db, sale, user_id=None)

        movimientos = db.execute(
            select(InventoryMovement).where(InventoryMovement.reference_id == sale.id)
        ).scalars().all()
        self.assertEqual(len(movimientos), 1)
        self.assertEqual(Decimal(movimientos[0].quantity), Decimal("60"))
        db.refresh(insumo)
        self.assertEqual(Decimal(insumo.current_stock), Decimal("940"))


class SelectionModeChangeIsNotRetroactiveTests(unittest.TestCase):
    """US6, FR-013 (hallazgo de `/speckit-analyze`): cambiar `selection_mode`/los
    topes de un grupo ya usado por un pedido confirmado no altera el precio, las
    opciones ni el consumo de inventario ya registrados en ese pedido."""

    def test_cambiar_el_modo_de_un_grupo_no_toca_un_pedido_ya_confirmado(self):
        db = fx.new_session()
        variant = fx.make_variant(db, price=PRECIO)
        group = fx.make_option_group(db, selection_mode="conteo", min_select=1, max_select=1)
        fx.link_variant_group(db, variant, group, min_select=1, max_select=1)
        insumo = fx.make_inventory_item(db, current_stock=Decimal("1000"))
        opt = fx.make_option(
            db, group=group, extra_price=Decimal("500"),
            inventory_item_id=insumo.id, item_quantity=Decimal("10"),
        )
        db.commit()

        data = OrderCreate(
            channel=OrderChannel.POS,
            items=[OrderItemIn(
                product_variant_id=variant.id, quantity=1,
                options=[OptionSelectionIn(option_id=opt.id)],
            )],
        )
        order = orders_service.create_order(db, data, uuid4())
        item = order.items[0]
        unit_price_antes = item.unit_price
        option_ids_antes = sorted(o.option_id for o in item.options)
        movimientos_antes = sorted(
            (m.inventory_item_id, str(m.quantity))
            for m in db.execute(
                select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
            ).scalars().all()
        )

        # Cambiar el grupo a "cantidad" con topes, vía PATCH /option-groups/{id}.
        update_option_group(
            group.id,
            OptionGroupUpdate(selection_mode="cantidad", max_quantity_per_option=3),
            db, None,
        )
        db.commit()

        db.refresh(order)
        db.refresh(item)
        self.assertEqual(item.unit_price, unit_price_antes)
        self.assertEqual(sorted(o.option_id for o in item.options), option_ids_antes)
        movimientos_despues = sorted(
            (m.inventory_item_id, str(m.quantity))
            for m in db.execute(
                select(InventoryMovement).where(InventoryMovement.reference_id == order.id)
            ).scalars().all()
        )
        self.assertEqual(movimientos_despues, movimientos_antes)


if __name__ == "__main__":
    unittest.main()
