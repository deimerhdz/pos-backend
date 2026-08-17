"""CONGELA app/api/v1/catalog/line_pricing.py — motor de precio de línea y
validación de selección de opciones (módulo `catalog`, criticidad ALTA).

Referencias de evidencia: `specs/000-reconocimiento/reglas-de-negocio.md`
sección 2 (RN-CAT-*) y `specs/000-reconocimiento/registro-de-anomalias.md`
(A-04, A-06) del repositorio `pos-specs`.
"""
import unittest
from decimal import Decimal

from fastapi import HTTPException

from app.characterization_tests import fixtures as f
from app.catalog_engine import (
    check_availability,
    compute_line_price,
    load_valid_options,
    validate_option_selection,
)


class ComputeLinePriceTests(unittest.TestCase):
    """RN-CAT-01: precio de línea = precio de variante + suma de extra_price."""

    def setUp(self):
        self.db = f.new_session()

    def test_rn_cat_01_precio_variante_mas_extras(self):
        variant = f.make_variant(self.db, price=Decimal("15000"))
        group = f.make_option_group(self.db)
        chocolate = f.make_option(self.db, group=group, extra_price=Decimal("1000"))
        mani = f.make_option(self.db, group=group, extra_price=Decimal("500"))
        total = compute_line_price(variant, [chocolate, mani])
        self.assertEqual(total, Decimal("16500"))

    def test_rn_cat_01_sin_opciones_es_solo_el_precio_de_la_variante(self):
        variant = f.make_variant(self.db, price=Decimal("9999.99"))
        self.assertEqual(compute_line_price(variant, []), Decimal("9999.99"))

    def test_rn_cat_02_no_hay_redondeo_explicito_suma_decimal_exacta(self):
        """RN-CAT-02 (DUDOSA): no hay quantize/round; la suma es Decimal exacta."""
        variant = f.make_variant(self.db, price=Decimal("100.01"))
        group = f.make_option_group(self.db)
        opt = f.make_option(self.db, group=group, extra_price=Decimal("0.02"))
        total = compute_line_price(variant, [opt])
        self.assertEqual(total, Decimal("100.03"))
        # Ninguna reducción de escala: 3 opciones de 0.001 no se pierden por redondeo.
        variant2 = f.make_variant(self.db, price=Decimal("0"))
        opts = [f.make_option(self.db, group=group, extra_price=Decimal("0.001")) for _ in range(3)]
        self.assertEqual(compute_line_price(variant2, opts), Decimal("0.003"))


class CheckAvailabilityTests(unittest.TestCase):
    """RN-CAT-24: stock insuficiente es `<`, estricto (no `<=`). RN-CAT-25:
    un requerido <=0 se omite del chequeo."""

    def setUp(self):
        self.db = f.new_session()

    def test_rn_cat_24_stock_exactamente_igual_al_requerido_no_bloquea(self):
        item = f.make_inventory_item(self.db, current_stock=Decimal("120"))
        # No debe lanzar: 120 < 120 es falso.
        check_availability(self.db, {item.id: Decimal("120")})

    def test_rn_cat_24_stock_un_paso_por_debajo_bloquea_con_409(self):
        item = f.make_inventory_item(self.db, current_stock=Decimal("119"))
        with self.assertRaises(HTTPException) as ctx:
            check_availability(self.db, {item.id: Decimal("120")})
        self.assertEqual(ctx.exception.status_code, 409)

    def test_rn_cat_24_boundary_119_999_falla_120_000_pasa_120_001_falla(self):
        item = f.make_inventory_item(self.db, current_stock=Decimal("120.000"))
        with self.assertRaises(HTTPException):
            check_availability(self.db, {item.id: Decimal("120.001")})
        check_availability(self.db, {item.id: Decimal("120.000")})
        item2 = f.make_inventory_item(self.db, current_stock=Decimal("119.999"))
        with self.assertRaises(HTTPException):
            check_availability(self.db, {item2.id: Decimal("120.000")})

    def test_rn_cat_25_requerido_cero_se_omite(self):
        item = f.make_inventory_item(self.db, current_stock=Decimal("0"))
        # No lanza aunque el stock sea 0, porque need=0 se omite.
        check_availability(self.db, {item.id: Decimal("0")})

    def test_rn_cat_25_requerido_negativo_se_omite(self):
        item = f.make_inventory_item(self.db, current_stock=Decimal("0"))
        check_availability(self.db, {item.id: Decimal("-5")})

    def test_insumo_inexistente_en_bd_no_bloquea(self):
        """El chequeo hace `db.get(...)`; si no encuentra el insumo, sigue de
        largo sin error (comportamiento actual, no necesariamente deseado)."""
        import uuid
        check_availability(self.db, {uuid.uuid4(): Decimal("999")})


class ValidateOptionSelectionTests(unittest.TestCase):
    """RN-CAT-27 a RN-CAT-33, y A-04/A-06 del registro de anomalías."""

    def setUp(self):
        self.db = f.new_session()

    def _variante_con_grupo(self, *, min_select, max_select, quantity_per_option, item_qty_opcion=Decimal("0")):
        variant = f.make_variant(self.db)
        group = f.make_option_group(self.db, min_select=min_select, max_select=max_select)
        f.link_variant_group(
            self.db, variant, group,
            min_select=min_select, max_select=max_select,
            quantity_per_option=Decimal(quantity_per_option),
        )
        item = f.make_inventory_item(self.db) if item_qty_opcion else None
        return variant, group, item

    def test_rn_cat_27_grupo_normal_min_1_max_2_acepta_1(self):
        variant, group, _ = self._variante_con_grupo(min_select=1, max_select=2, quantity_per_option=0)
        opt = f.make_option(self.db, group=group)
        # No lanza: 1 opción está en [1,2].
        validate_option_selection(self.db, variant, [opt])

    def test_rn_cat_27_grupo_normal_min_1_max_2_de_3_es_un_problema_no_bloqueante(self):
        """RN-CAT-27 dice "se rechaza", pero el grupo del ejemplo no descuenta
        inventario: al ejecutar con la configuración real (RN-CAT-31,
        STRICT_OPTION_SELECTION=False por defecto), la violación se tolera —
        no lanza, solo se registra un warning. Transcrito tal como corre hoy,
        no como sugiere el enunciado de la regla."""
        variant, group, _ = self._variante_con_grupo(min_select=1, max_select=2, quantity_per_option=0)
        opts = [f.make_option(self.db, group=group) for _ in range(3)]
        validate_option_selection(self.db, variant, opts)  # no lanza (tolerado)

    def test_rn_cat_27_con_strict_option_selection_true_si_bloquea(self):
        """Con el flag en True (no el default), la misma violación de RN-CAT-27
        sí produce 422 — así se confirma que el mensaje de la regla es real,
        solo que gobernado por RN-CAT-31."""
        from app.core.config import settings
        variant, group, _ = self._variante_con_grupo(min_select=1, max_select=2, quantity_per_option=0)
        opts = [f.make_option(self.db, group=group) for _ in range(3)]
        original = settings.STRICT_OPTION_SELECTION
        settings.STRICT_OPTION_SELECTION = True
        try:
            with self.assertRaises(HTTPException) as ctx:
                validate_option_selection(self.db, variant, opts)
            self.assertEqual(ctx.exception.status_code, 422)
            self.assertIn("como máximo 2", ctx.exception.detail["error"])
        finally:
            settings.STRICT_OPTION_SELECTION = original

    def test_rn_cat_28_grupo_obligatorio_que_descuenta_exige_exactamente_el_maximo(self):
        """«Copa Grande», 3 bolas: min_select=3, max_select=3, quantity_per_option=120.
        Elegir solo 1 sabor se rechaza, aunque cumpla el mínimo (que es el máximo)."""
        variant, group, _ = self._variante_con_grupo(min_select=3, max_select=3, quantity_per_option=120)
        opt = f.make_option(self.db, group=group)
        with self.assertRaises(HTTPException) as ctx:
            validate_option_selection(self.db, variant, [opt])
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("exactamente 3", ctx.exception.detail["error"])

    def test_rn_cat_28_grupo_obligatorio_que_descuenta_acepta_exactamente_el_maximo(self):
        variant, group, _ = self._variante_con_grupo(min_select=3, max_select=3, quantity_per_option=120)
        opts = [f.make_option(self.db, group=group) for _ in range(3)]
        validate_option_selection(self.db, variant, opts)  # no lanza

    def test_rn_cat_28_grupo_obligatorio_menor_a_min_igual_a_max_tambien_exige_max_no_min(self):
        """min_select=2, max_select=3, quantity_per_option>0: sigue exigiendo el
        MAXIMO (3), no basta con cumplir el mínimo (2)."""
        variant, group, _ = self._variante_con_grupo(min_select=2, max_select=3, quantity_per_option=50)
        opts = [f.make_option(self.db, group=group) for _ in range(2)]
        with self.assertRaises(HTTPException) as ctx:
            validate_option_selection(self.db, variant, opts)
        self.assertIn("exactamente 3", ctx.exception.detail["error"])

    def test_rn_cat_29_grupo_obligatorio_no_elegido_pide_el_maximo_si_descuenta(self):
        variant, group, _ = self._variante_con_grupo(min_select=1, max_select=2, quantity_per_option=80)
        with self.assertRaises(HTTPException) as ctx:
            validate_option_selection(self.db, variant, [])
        self.assertIn("elige 2 opción(es)", ctx.exception.detail["error"])

    def test_rn_cat_29_grupo_obligatorio_no_elegido_que_no_descuenta_se_tolera_con_strict_false(self):
        """Igual que RN-CAT-27: el ejemplo de RN-CAT-29 para un grupo que NO
        descuenta inventario también queda bajo el paraguas de RN-CAT-31 —
        con STRICT_OPTION_SELECTION=False (default) no lanza, solo advierte."""
        variant, group, _ = self._variante_con_grupo(min_select=1, max_select=2, quantity_per_option=0)
        validate_option_selection(self.db, variant, [])  # no lanza (tolerado)

    def test_rn_cat_30_violacion_en_grupo_que_descuenta_bloquea_aunque_strict_false(self):
        from app.core.config import settings
        self.assertFalse(settings.STRICT_OPTION_SELECTION, "precondición: default actual es False")
        variant, group, _ = self._variante_con_grupo(min_select=3, max_select=3, quantity_per_option=120)
        opt = f.make_option(self.db, group=group)
        with self.assertRaises(HTTPException):
            validate_option_selection(self.db, variant, [opt])

    def test_rn_cat_31_grupo_que_no_descuenta_tolera_violacion_con_strict_false(self):
        """Grupo «Toppings» obligatorio (min=1) sin quantity_per_option ni
        item_quantity: con STRICT_OPTION_SELECTION=False (default), no elegir
        nada no lanza — solo se loguea un warning."""
        variant, group, _ = self._variante_con_grupo(min_select=1, max_select=2, quantity_per_option=0)
        validate_option_selection(self.db, variant, [])  # no lanza

    def test_rn_cat_32_a06_opcion_de_grupo_ajeno_a_la_variante_se_tolera_con_strict_false(self):
        """A-06: con STRICT_OPTION_SELECTION=False, una opción de un grupo que la
        variante NO ofrece en absoluto no bloquea `validate_option_selection`."""
        variant = f.make_variant(self.db)
        grupo_ajeno = f.make_option_group(self.db)
        opcion_ajena = f.make_option(self.db, group=grupo_ajeno, extra_price=Decimal("3000"))
        validate_option_selection(self.db, variant, [opcion_ajena])  # no lanza

    def test_rn_cat_33_a04_sin_pasar_variant_load_valid_options_no_valida_nada(self):
        """A-04 / RN-CAT-33: `load_valid_options` sin `variant=` no valida
        min/max/pertenencia en absoluto — es exactamente el mecanismo de la
        regresión de `add_item_to_table` documentada en A-04."""
        group = f.make_option_group(self.db, min_select=3, max_select=3)
        # Grupo exige exactamente 3, pero no se pasa `variant`.
        opt = f.make_option(self.db, group=group)
        options = load_valid_options(self.db, [opt.id])  # sin variant=
        self.assertEqual(len(options), 1)

    def test_load_valid_options_con_variant_si_aplica_la_validacion(self):
        variant, group, _ = self._variante_con_grupo(min_select=3, max_select=3, quantity_per_option=120)
        opt = f.make_option(self.db, group=group)
        with self.assertRaises(HTTPException):
            load_valid_options(self.db, [opt.id], variant=variant)

    def test_load_valid_options_deduplica_ids_repetidos(self):
        group = f.make_option_group(self.db)
        opt = f.make_option(self.db, group=group)
        options = load_valid_options(self.db, [opt.id, opt.id, opt.id])
        self.assertEqual(len(options), 1)

    def test_load_valid_options_opcion_inactiva_lanza_422(self):
        group = f.make_option_group(self.db)
        opt = f.make_option(self.db, group=group, active=False)
        with self.assertRaises(HTTPException) as ctx:
            load_valid_options(self.db, [opt.id])
        self.assertEqual(ctx.exception.status_code, 422)

    def test_load_valid_options_opcion_inexistente_lanza_404(self):
        import uuid
        with self.assertRaises(HTTPException) as ctx:
            load_valid_options(self.db, [uuid.uuid4()])
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
