"""Spec 093-cajero-carta-agotado (User Story 3, escenario 11): un Cajero no puede
crear, editar ni desactivar un producto -- `POST`/`PATCH`/`PUT`/`DELETE /products(/{id})`
siguen exclusivos de `require_tenant_admin`, sin cambios de esta spec (FR-024). Deja
constancia explícita de la garantía que la spec exige sobre comportamiento ya
existente, para que una futura relajación accidental de `require_tenant_admin` (p. ej.
al servicio del interruptor "Agotado") no pase desapercibida.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_products_cashier_forbidden -v
"""
import unittest

from fastapi import HTTPException

from app.characterization_tests import product_availability_fixtures as fx
from app.core.dependencies import require_tenant_admin


class ProductsCashierForbiddenTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        self.tenant = fx.make_tenant(self.db)

    def _user(self, role_name: str):
        role = fx.make_role(self.db, name=role_name)
        return fx.make_user(self.db, tenant=self.tenant, role=role)

    def test_cashier_no_alcanza_require_tenant_admin(self):
        """`create_product`/`update_product`/`soft_delete` (POST/PATCH/PUT/DELETE
        `/products`) dependen todos de `require_tenant_admin` -- un Cajero nunca
        llega a esos service methods porque la dependencia ya lo rechaza antes."""
        cajero = self._user("CASHIER")
        with self.assertRaises(HTTPException) as ctx:
            require_tenant_admin(cajero)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_admin_sigue_con_acceso_sin_cambios(self):
        admin = self._user("ADMIN")
        self.assertIs(require_tenant_admin(admin), admin)


if __name__ == "__main__":
    unittest.main()
