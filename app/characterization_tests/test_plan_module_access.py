"""Tests de la nueva funcionalidad — spec 033-planes-suscripcion-tenant,
Historia de Usuario 4: el sistema deniega el acceso a módulos que el plan no
incluye (FR-008/FR-009).

No son characterization tests — comportamiento enteramente nuevo. Ejercita
`require_module_access(...)` directamente (mismo mecanismo que
`inventory/router.py`/`promotions/router.py` ya conectan a nivel de router,
T040-T042) para no depender de la resolución de `Depends()` de FastAPI.

    python -m unittest app.characterization_tests.test_plan_module_access -v
"""
import unittest

from fastapi import HTTPException

from app.characterization_tests import plan_fixtures as fx
from app.core.plan_limits import require_module_access


class TestPlanModuleAccess(unittest.TestCase):
    # ---------------------------------------------------------- Acceptance Scenario 1

    def test_modulo_no_incluido_deniega_con_mensaje_claro(self):
        """research.md Decisión 6: 'compras' se gatea independientemente de
        'inventario' aunque ambos vivan en el mismo router."""
        db = fx.new_session()
        plan = fx.make_plan(db, inventario_access=True, compras_access=False)
        tenant = fx.make_tenant(db, plan=plan)
        user = fx.make_user(db, tenant)
        db.commit()

        # inventario sí incluido
        require_module_access("inventario")(tenant=tenant, db=db, _user=user)

        with self.assertRaises(HTTPException) as ctx:
            require_module_access("compras")(tenant=tenant, db=db, _user=user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("compras", ctx.exception.detail.lower())

    # ---------------------------------------------------------- Acceptance Scenario 2

    def test_modulo_incluido_se_concede_sin_restriccion(self):
        db = fx.new_session()
        plan = fx.make_plan(db, promociones_access=True)
        tenant = fx.make_tenant(db, plan=plan)
        user = fx.make_user(db, tenant)
        db.commit()

        require_module_access("promociones")(tenant=tenant, db=db, _user=user)  # no lanza

    def test_modulo_no_configurado_queda_bloqueado_por_defecto(self):
        """FR-002: una característica de módulo no configurada al crear el
        plan se comporta como acceso denegado."""
        db = fx.new_session()
        plan = fx.make_plan(db)  # ningún módulo configurado
        tenant = fx.make_tenant(db, plan=plan)
        user = fx.make_user(db, tenant)
        db.commit()

        for module in ("inventario", "compras", "promociones"):
            with self.assertRaises(HTTPException):
                require_module_access(module)(tenant=tenant, db=db, _user=user)


if __name__ == "__main__":
    unittest.main()
