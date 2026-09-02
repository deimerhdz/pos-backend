"""Tests de la nueva funcionalidad (spec 065): `selection_mode` ("conteo"/"cantidad") y sus
dos topes opcionales en `OptionGroup`. A diferencia del resto de `characterization_tests/`,
estos no "congelan" comportamiento heredado -- verifican comportamiento NUEVO definido en
`specs/065-opciones-por-cantidad/spec.md` (FR-001, FR-008, FR-009).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_catalog_option_groups_selection_mode -v
"""
import unittest

from app.characterization_tests import fixtures as f
from app.api.v1.catalog.router import create_option_group, update_option_group
from app.api.v1.catalog.schemas import OptionGroupCreate, OptionGroupUpdate


class CreateOptionGroupSelectionModeTests(unittest.TestCase):
    """FR-001: `selection_mode` es opcional, con default "conteo"."""

    def setUp(self):
        self.db = f.new_session()

    def test_grupo_sin_selection_mode_queda_en_conteo(self):
        group = create_option_group(
            OptionGroupCreate(name="Sabores", min_select=1, max_select=1, pricing_type="incluido"),
            self.db, None,
        )
        self.assertEqual(group.selection_mode, "conteo")
        self.assertIsNone(group.max_quantity_per_option)
        self.assertIsNone(group.max_total_quantity)

    def test_grupo_cantidad_se_crea_con_sus_topes(self):
        group = create_option_group(
            OptionGroupCreate(
                name="Toppings", min_select=0, max_select=1, pricing_type="con_recargo",
                selection_mode="cantidad", max_quantity_per_option=3, max_total_quantity=5,
            ),
            self.db, None,
        )
        self.assertEqual(group.selection_mode, "cantidad")
        self.assertEqual(group.max_quantity_per_option, 3)
        self.assertEqual(group.max_total_quantity, 5)

    def test_grupo_cantidad_sin_topes_queda_sin_limite_propio(self):
        group = create_option_group(
            OptionGroupCreate(
                name="Toppings libres", min_select=0, max_select=1, pricing_type="con_recargo",
                selection_mode="cantidad",
            ),
            self.db, None,
        )
        self.assertIsNone(group.max_quantity_per_option)
        self.assertIsNone(group.max_total_quantity)


class UpdateOptionGroupSelectionModeTests(unittest.TestCase):
    """`PATCH /option-groups/{id}` persiste `selection_mode` y los topes."""

    def setUp(self):
        self.db = f.new_session()

    def test_actualizar_modo_y_topes_de_un_grupo_existente(self):
        group = f.make_option_group(self.db)
        self.db.commit()
        updated = update_option_group(
            group.id,
            OptionGroupUpdate(
                selection_mode="cantidad", max_quantity_per_option=3, max_total_quantity=5,
            ),
            self.db, None,
        )
        self.assertEqual(updated.selection_mode, "cantidad")
        self.assertEqual(updated.max_quantity_per_option, 3)
        self.assertEqual(updated.max_total_quantity, 5)

    def test_no_enviar_selection_mode_no_toca_el_valor_actual(self):
        group = f.make_option_group(self.db, selection_mode="cantidad", max_quantity_per_option=2)
        self.db.commit()
        updated = update_option_group(
            group.id, OptionGroupUpdate(name="Renombrado"), self.db, None,
        )
        self.assertEqual(updated.selection_mode, "cantidad")
        self.assertEqual(updated.max_quantity_per_option, 2)


if __name__ == "__main__":
    unittest.main()
