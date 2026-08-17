"""CONGELA app/api/v1/catalog/consumption_plan.py — qué descuenta una línea
vendida del inventario (módulo `catalog`, criticidad ALTA).

Referencias: `specs/000-reconocimiento/reglas-de-negocio.md` sección 2
(RN-CAT-*) y `specs/000-reconocimiento/registro-de-anomalias.md` (A-02
[PROTEGIDA], A-03, A-06) del repositorio `pos-specs`.
"""
import unittest
from decimal import Decimal

from fastapi import HTTPException

from app.characterization_tests import fixtures as f
from app.catalog_engine import (
    ensure_lines_consume_inventory,
    group_discounts,
    plan_line_consumption,
    required_consumption,
)
from app.catalog_engine import grupos_que_descuentan as grupos_que_descuentan_lp


class PlanLineConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.db = f.new_session()

    def test_rn_cat_17_receta_fija_es_cantidad_receta_por_cantidad_vendida(self):
        variant = f.make_variant(self.db)
        insumo = f.make_inventory_item(self.db)
        f.make_recipe_item(self.db, variant, insumo, quantity=Decimal("1"))
        lines = plan_line_consumption(self.db, variant.id, 3, [])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].quantity, Decimal("3"))
        self.assertEqual(lines[0].source, "receta")

    def test_a02_protegida_rn_cat_18_el_tamano_manda_sobre_la_opcion_nunca_se_suman(self):
        """A-02 [PROTEGIDA]: «Copa Grande» ofrece «Sabores» con
        quantity_per_option=120 (g). Opción «Fresa» con item_quantity=80.
        Vender 1 copa con «Fresa»: consumo = 120 g (el tamaño manda), NO 200 g
        (suma) ni 80 g (solo la opción). Es el bug histórico del doble
        descuento (140g) que este comportamiento corrige — no tocar."""
        variant = f.make_variant(self.db)
        insumo = f.make_inventory_item(self.db)
        group = f.make_option_group(self.db, min_select=1, max_select=1)
        f.link_variant_group(self.db, variant, group, quantity_per_option=Decimal("120"))
        fresa = f.make_option(self.db, group=group, inventory_item_id=insumo.id, item_quantity=Decimal("80"))

        lines = plan_line_consumption(self.db, variant.id, 1, [fresa])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].quantity, Decimal("120"))
        self.assertEqual(lines[0].source, "variante")
        self.assertNotEqual(lines[0].quantity, Decimal("200"))  # nunca se suman

    def test_rn_cat_18_si_el_tamano_no_define_nada_manda_la_opcion(self):
        variant = f.make_variant(self.db)
        insumo = f.make_inventory_item(self.db)
        group = f.make_option_group(self.db, min_select=1, max_select=1)
        f.link_variant_group(self.db, variant, group, quantity_per_option=Decimal("0"))
        opt = f.make_option(self.db, group=group, inventory_item_id=insumo.id, item_quantity=Decimal("50"))
        lines = plan_line_consumption(self.db, variant.id, 1, [opt])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].quantity, Decimal("50"))
        self.assertEqual(lines[0].source, "opcion")

    def test_a03_discrepancia_docstring_del_modelo_contradice_el_codigo_real(self):
        """A-03: el docstring de `VariantOptionGroup.quantity_per_option` dice
        "Se suma a `options.item_quantity`", pero RN-CAT-18/A-02 demuestran que
        el código real SUSTITUYE, no suma. Se congelan ambos hechos: el texto
        obsoleto, y el comportamiento real que lo contradice."""
        from app.models.variant_option_group import VariantOptionGroup
        import inspect
        src = inspect.getsource(VariantOptionGroup)
        self.assertIn("Se suma a", src, "el comentario obsoleto sigue en el modelo (A-03 no corregida)")
        # Y el comportamiento real (ya congelado en el test de A-02) es lo opuesto.

    def test_rn_cat_20_por_cada_opcion_elegida_no_total_repartido(self):
        """3 sabores de 120g cada uno -> 3 líneas de 120, total 360, no 120
        repartido entre las tres."""
        variant = f.make_variant(self.db)
        insumo1, insumo2, insumo3 = (f.make_inventory_item(self.db) for _ in range(3))
        group = f.make_option_group(self.db, min_select=3, max_select=3)
        f.link_variant_group(self.db, variant, group, quantity_per_option=Decimal("120"))
        opts = [
            f.make_option(self.db, group=group, inventory_item_id=i.id, item_quantity=Decimal("0"))
            for i in (insumo1, insumo2, insumo3)
        ]
        lines = plan_line_consumption(self.db, variant.id, 1, opts)
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(l.quantity == Decimal("120") for l in lines))
        self.assertEqual(sum(l.quantity for l in lines), Decimal("360"))

    def test_rn_cat_21_dos_opciones_al_mismo_insumo_generan_dos_lineas_separadas(self):
        variant = f.make_variant(self.db)
        insumo = f.make_inventory_item(self.db)
        group = f.make_option_group(self.db, min_select=2, max_select=2)
        f.link_variant_group(self.db, variant, group, quantity_per_option=Decimal("80"))
        fresa = f.make_option(self.db, group=group, inventory_item_id=insumo.id)
        fresa_premium = f.make_option(self.db, group=group, inventory_item_id=insumo.id)
        lines = plan_line_consumption(self.db, variant.id, 1, [fresa, fresa_premium])
        self.assertEqual(len(lines), 2, "no se fusionan en un solo movimiento")
        self.assertEqual({l.inventory_item_id for l in lines}, {insumo.id})

    def test_rn_cat_22_opcion_sin_insumo_ligado_no_genera_consumo(self):
        variant = f.make_variant(self.db)
        group = f.make_option_group(self.db)
        f.link_variant_group(self.db, variant, group, quantity_per_option=Decimal("0"))
        sin_topping = f.make_option(
            self.db, group=group, inventory_item_id=None, item_quantity=Decimal("50")
        )
        lines = plan_line_consumption(self.db, variant.id, 1, [sin_topping])
        self.assertEqual(lines, [])

    def test_rn_cat_23_cantidad_resultante_cero_no_genera_linea(self):
        variant = f.make_variant(self.db)
        insumo = f.make_inventory_item(self.db)
        group = f.make_option_group(self.db)
        f.link_variant_group(self.db, variant, group, quantity_per_option=Decimal("0"))
        opt = f.make_option(self.db, group=group, inventory_item_id=insumo.id, item_quantity=Decimal("0"))
        lines = plan_line_consumption(self.db, variant.id, 1, [opt])
        self.assertEqual(lines, [])

    def test_required_consumption_agrega_por_insumo(self):
        variant = f.make_variant(self.db)
        insumo = f.make_inventory_item(self.db)
        f.make_recipe_item(self.db, variant, insumo, quantity=Decimal("2"))
        group = f.make_option_group(self.db, min_select=1, max_select=1)
        f.link_variant_group(self.db, variant, group, quantity_per_option=Decimal("3"))
        opt = f.make_option(self.db, group=group, inventory_item_id=insumo.id)
        req = required_consumption(self.db, variant.id, 5, [opt])
        # receta: 2*5=10; opcion (tamaño manda, =3): 3*5=15; total 25 del mismo insumo
        self.assertEqual(req[insumo.id], Decimal("25"))


class GroupDiscountsCriteriaTests(unittest.TestCase):
    """RN-CAT-39 [DISCREPANCIA]: `grupos_que_descuentan` (line_pricing) y
    `group_discounts` (consumption_plan) responden distinto a la misma
    pregunta ("¿este grupo descuenta inventario?") para una opción con
    `item_quantity>0` pero sin `inventory_item_id`."""

    def setUp(self):
        self.db = f.new_session()

    def test_rn_cat_39_discrepancia_opcion_con_item_quantity_pero_sin_insumo_ligado(self):
        variant = f.make_variant(self.db)
        group = f.make_option_group(self.db, min_select=1, max_select=1)
        link = f.link_variant_group(self.db, variant, group, quantity_per_option=Decimal("0"))
        f.make_option(
            self.db, group=group,
            inventory_item_id=None, item_quantity=Decimal("10"), active=True,
        )

        # group_discounts (consumption_plan): exige inventory_item_id no nulo -> False
        self.assertFalse(group_discounts(self.db, link))

        # grupos_que_descuentan (line_pricing): solo mira item_quantity>0, sin
        # exigir inventory_item_id -> SÍ lo cuenta como "que descuenta"
        consumen = grupos_que_descuentan_lp(self.db, [link])
        self.assertIn(group.id, consumen)

    def test_ambos_criterios_coinciden_cuando_la_opcion_tiene_insumo_ligado(self):
        variant = f.make_variant(self.db)
        insumo = f.make_inventory_item(self.db)
        group = f.make_option_group(self.db, min_select=1, max_select=1)
        link = f.link_variant_group(self.db, variant, group, quantity_per_option=Decimal("0"))
        f.make_option(self.db, group=group, inventory_item_id=insumo.id, item_quantity=Decimal("10"))
        self.assertTrue(group_discounts(self.db, link))
        self.assertIn(group.id, grupos_que_descuentan_lp(self.db, [link]))

    def test_grupos_que_descuentan_solo_vive_en_line_pricing_no_en_consumption_plan(self):
        """`grupos_que_descuentan` (plural, sobre una lista de links) solo
        existe en `line_pricing.py`; `consumption_plan.py` define su propia
        función distinta, `group_discounts` (singular, sobre un solo link) —
        dos nombres para dos funciones con criterio distinto (RN-CAT-39), no
        una sola reexportada. Se congela para no confundirlas al mantener
        este test."""
        import app.api.v1.catalog.consumption_plan as cp
        self.assertFalse(hasattr(cp, "grupos_que_descuentan"))


class EnsureLinesConsumeInventoryTests(unittest.TestCase):
    """RN-CAT-34 y RN-CAT-35 [DISCREPANCIA]."""

    def setUp(self):
        self.db = f.new_session()

    def test_rn_cat_34_variante_sin_receta_ni_grupo_bloquea_con_409_sin_receta(self):
        variant = f.make_variant(self.db)
        with self.assertRaises(HTTPException) as ctx:
            ensure_lines_consume_inventory(self.db, [(variant.id, 1, [])])
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("no tiene receta configurada", ctx.exception.detail["error"])

    def test_rn_cat_35_discrepancia_grupo_opcional_unica_fuente_sin_elegir_bloquea_sin_eleccion(self):
        """El docstring de `ensure_lines_consume_inventory` describe no elegir
        un grupo opcional como "una decisión legítima del comensal", pero
        cuando ese grupo opcional es la ÚNICA fuente de consumo configurada,
        el código bloquea igual con 409 "sin_eleccion" — contradiciendo la
        prosa del propio comentario (A-06/RN-CAT-35)."""
        variant = f.make_variant(self.db)
        insumo = f.make_inventory_item(self.db)
        group = f.make_option_group(self.db, min_select=0, max_select=1)
        f.link_variant_group(self.db, variant, group, min_select=0, max_select=1, quantity_per_option=Decimal("80"))
        f.make_option(self.db, group=group, inventory_item_id=insumo.id)  # existe, pero no se elige

        with self.assertRaises(HTTPException) as ctx:
            ensure_lines_consume_inventory(self.db, [(variant.id, 1, [])])
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("no se eligió ninguna", ctx.exception.detail["error"])
        self.assertIn("variantes_sin_opcion", ctx.exception.detail)

    def test_variante_con_receta_fija_y_grupo_opcional_sin_elegir_no_bloquea(self):
        """Contraste explícito de RN-CAT-35: si YA hay receta fija, un grupo
        opcional sin elegir sí es una decisión legítima y no bloquea."""
        variant = f.make_variant(self.db)
        insumo_fijo = f.make_inventory_item(self.db)
        f.make_recipe_item(self.db, variant, insumo_fijo, quantity=Decimal("1"))

        insumo_opt = f.make_inventory_item(self.db)
        group = f.make_option_group(self.db, min_select=0, max_select=1)
        f.link_variant_group(self.db, variant, group, min_select=0, max_select=1, quantity_per_option=Decimal("80"))
        f.make_option(self.db, group=group, inventory_item_id=insumo_opt.id)

        ensure_lines_consume_inventory(self.db, [(variant.id, 1, [])])  # no lanza

    def test_rn_cat_34_variante_con_grupo_obligatorio_que_descuenta_pero_ninguna_opcion_elegida(self):
        variant = f.make_variant(self.db)
        insumo = f.make_inventory_item(self.db)
        group = f.make_option_group(self.db, min_select=1, max_select=1)
        f.link_variant_group(self.db, variant, group, min_select=1, max_select=1, quantity_per_option=Decimal("80"))
        f.make_option(self.db, group=group, inventory_item_id=insumo.id)

        with self.assertRaises(HTTPException) as ctx:
            ensure_lines_consume_inventory(self.db, [(variant.id, 1, [])])
        self.assertIn("variantes_sin_opcion", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
