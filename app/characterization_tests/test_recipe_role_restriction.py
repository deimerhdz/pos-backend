"""Spec 093-cajero-carta-agotado (User Story 3, escenario 12): `GET
/variants/{variant_id}/recipe` deja de ser alcanzable para el rol CASHIER (FR-025,
research.md D8) -- la receta (BOM) expone `inventory_item_id`, dato de inventario sin
ningún propósito en la toma de pedidos. El Administrador conserva acceso sin cambios.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_recipe_role_restriction -v
"""
import unittest

from fastapi import HTTPException

from app.characterization_tests import product_availability_fixtures as fx
from app.core.dependencies import forbid_cashier


class RecipeRoleRestrictionTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        self.tenant = fx.make_tenant(self.db)

    def _user(self, role_name: str):
        role = fx.make_role(self.db, name=role_name)
        return fx.make_user(self.db, tenant=self.tenant, role=role)

    def test_cashier_403(self):
        cajero = self._user("CASHIER")
        with self.assertRaises(HTTPException) as ctx:
            forbid_cashier(cajero)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_admin_sigue_con_acceso(self):
        admin = self._user("ADMIN")
        self.assertIs(forbid_cashier(admin), admin)


if __name__ == "__main__":
    unittest.main()
