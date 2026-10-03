"""Spec 093-cajero-carta-agotado (User Story 1 y 3): `ProductService.set_availability`,
el único camino por el que Cajero y Administrador tocan `Product.available` (research.md
D3/D4/D5/D10).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_product_availability_toggle -v
"""
import unittest

from fastapi import HTTPException

from app.characterization_tests import product_availability_fixtures as fx
from app.api.v1.products.service import ProductService
from app.core.dependencies import require_tenant_admin_or_cashier


class SetAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        self.tenant = fx.make_tenant(self.db)
        self.service = ProductService()

    def _user(self, role_name: str):
        role = fx.make_role(self.db, name=role_name)
        return fx.make_user(self.db, tenant=self.tenant, role=role)

    # -- Escenario 3/4: marcar y desmarcar ------------------------------------

    def test_cajero_marca_producto_activo_como_agotado(self):
        """Escenario 3: `available` pasa a False, queda `available_changed_at`/
        `available_changed_by_name`, y se registra en AuditLog con el usuario."""
        cajero = self._user("CASHIER")
        product = fx.make_product(self.db, available=True, active=True)
        self.db.commit()

        updated = self.service.set_availability(self.db, product.id, False, cajero)

        self.assertFalse(updated.available)
        self.assertIsNotNone(updated.available_changed_at)
        self.assertEqual(updated.available_changed_by_name, cajero.name)
        log = fx.latest_audit_log(self.db, entity="product", entity_id=product.id)
        self.assertIsNotNone(log)
        self.assertEqual(log.action, "availability_changed")
        self.assertEqual(log.user_id, cajero.id)
        self.assertEqual(log.payload, {"available": False, "previous": True})

    def test_volver_a_marcar_disponible(self):
        """Escenario 4: desmarcar vuelve `available` a True."""
        admin = self._user("ADMIN")
        product = fx.make_product(self.db, available=False, active=True)
        self.db.commit()

        updated = self.service.set_availability(self.db, product.id, True, admin)

        self.assertTrue(updated.available)

    # -- Escenario 10 / FR-011: producto inactivo -----------------------------

    def test_no_se_puede_cambiar_disponibilidad_de_producto_inactivo(self):
        cajero = self._user("CASHIER")
        product = fx.make_product(self.db, available=True, active=False)
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            self.service.set_availability(self.db, product.id, False, cajero)

        self.assertEqual(ctx.exception.status_code, 409)
        self.db.refresh(product)
        self.assertTrue(product.available)  # sin cambios

    # -- Escenario 11/403: solo ADMIN/CASHIER llegan a este endpoint ----------

    def test_require_tenant_admin_or_cashier_permite_admin_y_cajero(self):
        for role_name in ("ADMIN", "CASHIER"):
            user = self._user(role_name)
            self.assertIs(require_tenant_admin_or_cashier(user), user)

    def test_require_tenant_admin_or_cashier_bloquea_otros_roles(self):
        mesero = self._user("MESERO")
        with self.assertRaises(HTTPException) as ctx:
            require_tenant_admin_or_cashier(mesero)
        self.assertEqual(ctx.exception.status_code, 403)

    # -- Escenario 16 / FR-026: aislamiento por tenant ------------------------

    def test_producto_de_otro_tenant_responde_404(self):
        """Dos sesiones independientes simulan dos tenants reales (cada schema de
        tenant es, en producción, una base distinta): un producto creado en la
        sesión B no existe para la sesión A."""
        db_a = fx.new_session()
        db_b = fx.new_session()
        product_b = fx.make_product(db_b)
        db_b.commit()

        with self.assertRaises(HTTPException) as ctx:
            self.service.get_or_404(db_a, product_b.id)
        self.assertEqual(ctx.exception.status_code, 404)

    # -- Escenario 14 / FR-016: última petición gana --------------------------

    def test_dos_cambios_sucesivos_dejan_el_ultimo_valor(self):
        """Dos peticiones casi simultáneas, procesadas una tras otra por el
        servidor (un único `UPDATE` cada vez, sin bloqueo, research.md D10): el
        resultado final es el de la última, sin estado intermedio inconsistente."""
        cajero = self._user("CASHIER")
        otro_cajero = self._user("CASHIER")
        product = fx.make_product(self.db, available=True, active=True)
        self.db.commit()

        self.service.set_availability(self.db, product.id, False, cajero)
        final = self.service.set_availability(self.db, product.id, True, otro_cajero)

        self.assertTrue(final.available)
        self.assertEqual(final.available_changed_by_name, otro_cajero.name)


if __name__ == "__main__":
    unittest.main()
