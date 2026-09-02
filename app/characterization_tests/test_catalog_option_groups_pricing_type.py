"""Tests de la nueva funcionalidad (spec 064): tipo de precio ("incluido"/"con_recargo")
en `OptionGroup`. A diferencia del resto de `characterization_tests/`, estos no "congelan"
comportamiento heredado -- verifican comportamiento NUEVO definido en
`specs/064-grupos-opciones-precio-inventario/spec.md` (FR-001 a FR-005, FR-015).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_catalog_option_groups_pricing_type -v
"""
import unittest
from decimal import Decimal

from fastapi import HTTPException

from app.characterization_tests import fixtures as f
from app.api.v1.catalog.router import (
    create_option_group,
    update_option_group,
    add_option,
    update_option,
)
from app.api.v1.catalog.schemas import (
    OptionGroupCreate,
    OptionGroupUpdate,
    OptionCreate,
    OptionUpdate,
)
from app.catalog_engine.core import ChosenOption, compute_line_price


class CreateOptionGroupPricingTypeTests(unittest.TestCase):
    """FR-001: todo grupo de opciones nuevo exige un `pricing_type` explícito."""

    def setUp(self):
        self.db = f.new_session()

    def test_grupo_incluido_se_crea_con_normalidad(self):
        group = create_option_group(
            OptionGroupCreate(name="Sabores", min_select=1, max_select=1, pricing_type="incluido"),
            self.db, None,
        )
        self.assertEqual(group.pricing_type, "incluido")

    def test_grupo_con_recargo_se_crea_con_normalidad(self):
        group = create_option_group(
            OptionGroupCreate(name="Toppings", min_select=0, max_select=2, pricing_type="con_recargo"),
            self.db, None,
        )
        self.assertEqual(group.pricing_type, "con_recargo")

    def test_pricing_type_ausente_es_rechazado_por_el_schema(self):
        with self.assertRaises(Exception):
            OptionGroupCreate(name="Sin tipo", min_select=0, max_select=1)


class AddOptionPricingTypeTests(unittest.TestCase):
    """FR-002/FR-003: "incluido" bloquea precio != $0; "con_recargo" permite precio libre."""

    def setUp(self):
        self.db = f.new_session()
        self.tenant = f.make_tenant_stub()

    def test_opcion_con_precio_en_grupo_incluido_es_rechazada_con_422(self):
        group = f.make_option_group(self.db, pricing_type="incluido")
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            add_option(
                group.id, OptionCreate(name="Fresa", extra_price=Decimal("500")),
                self.db, self.tenant, None,
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_opcion_en_cero_en_grupo_incluido_se_crea_con_normalidad(self):
        group = f.make_option_group(self.db, pricing_type="incluido")
        self.db.commit()
        option = add_option(
            group.id, OptionCreate(name="Fresa", extra_price=Decimal("0")),
            self.db, self.tenant, None,
        )
        self.assertEqual(option.extra_price, Decimal("0"))

    def test_opcion_con_precio_en_grupo_con_recargo_se_crea_con_normalidad(self):
        group = f.make_option_group(self.db, pricing_type="con_recargo")
        self.db.commit()
        option = add_option(
            group.id, OptionCreate(name="Maní", extra_price=Decimal("500")),
            self.db, self.tenant, None,
        )
        self.assertEqual(option.extra_price, Decimal("500"))

    def test_actualizar_precio_a_valor_no_cero_en_grupo_incluido_es_rechazado(self):
        group = f.make_option_group(self.db, pricing_type="incluido")
        option = f.make_option(self.db, group=group, extra_price=Decimal("0"))
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            update_option(
                option.id, OptionUpdate(extra_price=Decimal("300")),
                self.db, self.tenant, None,
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_vender_con_opcion_de_grupo_incluido_no_suma_recargo(self):
        """Acceptance Scenario 2 de US1: el precio de línea es exactamente el de la
        presentación, sin importar que la opción de un grupo "incluido" esté elegida."""
        variant = f.make_variant(self.db, price=Decimal("15000"))
        group = f.make_option_group(self.db, pricing_type="incluido")
        option = f.make_option(self.db, group=group, extra_price=Decimal("0"))
        self.db.commit()
        self.assertEqual(compute_line_price(variant, [ChosenOption(option, 1)]), Decimal("15000"))


class UpdateOptionGroupPricingTypeTests(unittest.TestCase):
    """FR-004: reclasificar "con_recargo" -> "incluido" fuerza $0 en todas sus opciones;
    la reclasificación inversa no tiene efecto lateral sobre precios."""

    def setUp(self):
        self.db = f.new_session()

    def test_cambiar_a_incluido_fuerza_todas_las_opciones_a_cero(self):
        group = f.make_option_group(self.db, pricing_type="con_recargo")
        opt1 = f.make_option(self.db, group=group, name="Maní", extra_price=Decimal("500"))
        opt2 = f.make_option(self.db, group=group, name="Chispas", extra_price=Decimal("800"))
        self.db.commit()

        update_option_group(group.id, OptionGroupUpdate(pricing_type="incluido"), self.db, None)
        self.db.refresh(opt1)
        self.db.refresh(opt2)

        self.assertEqual(opt1.extra_price, Decimal("0"))
        self.assertEqual(opt2.extra_price, Decimal("0"))

    def test_cambiar_a_con_recargo_no_altera_precios_existentes(self):
        group = f.make_option_group(self.db, pricing_type="incluido")
        opt = f.make_option(self.db, group=group, extra_price=Decimal("0"))
        self.db.commit()

        update_option_group(group.id, OptionGroupUpdate(pricing_type="con_recargo"), self.db, None)
        self.db.refresh(opt)

        self.assertEqual(opt.extra_price, Decimal("0"))  # nadie lo tocó -- sigue en $0
        self.db.refresh(group)
        self.assertEqual(group.pricing_type, "con_recargo")

    def test_mantener_con_recargo_sin_cambiar_no_toca_ninguna_opcion(self):
        group = f.make_option_group(self.db, pricing_type="con_recargo")
        opt = f.make_option(self.db, group=group, extra_price=Decimal("500"))
        self.db.commit()

        update_option_group(group.id, OptionGroupUpdate(name=group.name), self.db, None)
        self.db.refresh(opt)

        self.assertEqual(opt.extra_price, Decimal("500"))


if __name__ == "__main__":
    unittest.main()
