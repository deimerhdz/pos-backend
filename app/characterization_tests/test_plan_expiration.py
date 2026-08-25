"""Tests de la nueva funcionalidad — spec 033-planes-suscripcion-tenant,
Historia de Usuario 5: el sistema bloquea automáticamente a un tenant cuyo
plan vence sin renovarse (FR-019/FR-020/FR-021).

No son characterization tests — comportamiento enteramente nuevo.

Este fichero se escribe ANTES de `ensure_plan_not_expired` (T048) a
propósito (tasks.md, Nota de alcance mínimo): debe FALLAR contra el código
de las Historias 1-4 tal cual, porque el bloqueo por vencimiento todavía no
existe — es la prueba de que esta historia agrega comportamiento real, no
solo documentación.

    python -m unittest app.characterization_tests.test_plan_expiration -v
"""
import unittest
from datetime import timedelta

from fastapi import HTTPException

from app.characterization_tests import plan_fixtures as fx
from app.core.plan_limits import enforce_plan_limit, require_module_access
from app.core.timezone import utc_now


def _vencido_tenant(db, **plan_kw):
    plan = fx.make_plan(db, mesas_limit=None, inventario_access=True, **plan_kw)
    tenant = fx.make_tenant(
        db, plan=plan, ciclo_facturacion="mensual",
        plan_iniciado_en=utc_now().replace(tzinfo=None) - timedelta(days=45),
    )
    return tenant


class TestPlanExpirationBlocksResourceLimits(unittest.TestCase):
    # ---------------------------------------------------------- Acceptance Scenario 1

    def test_tenant_vencido_bloquea_creacion_de_recurso_con_cupo_disponible(self):
        db = fx.new_session()
        tenant = _vencido_tenant(db)  # mesas_limit=None (ilimitado) — solo el vencimiento debe bloquear
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            enforce_plan_limit(db, tenant, "mesas")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("venci", ctx.exception.detail.lower())

    # ---------------------------------------------------------- Acceptance Scenario 2

    def test_tenant_vencido_deniega_acceso_a_modulo_incluido(self):
        db = fx.new_session()
        tenant = _vencido_tenant(db)  # inventario_access=True — solo el vencimiento debe bloquear
        user = fx.make_user(db, tenant)
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            require_module_access("inventario")(tenant=tenant, db=db, _user=user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("venci", ctx.exception.detail.lower())

    # ---------------------------------------------------------- Acceptance Scenario 3

    def test_vencimiento_no_borra_datos_existentes(self):
        db = fx.new_session()
        tenant = _vencido_tenant(db)
        fx.make_dining_table(db)
        db.commit()

        with self.assertRaises(HTTPException):
            enforce_plan_limit(db, tenant, "mesas")

        from sqlalchemy import func, select
        from app.models.dining_table import DiningTable

        count = db.execute(select(func.count()).select_from(DiningTable)).scalar_one()
        self.assertEqual(count, 1)  # sigue existiendo, nada se borró

    # ---------------------------------------------------------- Acceptance Scenario 4

    def test_sin_vencimiento_nunca_bloquea_por_este_motivo(self):
        db = fx.new_session()
        plan = fx.make_plan(db, mesas_limit=None, inventario_access=True)
        tenant = fx.make_tenant(db, plan=plan)  # ciclo_facturacion=None -> plan_vence_en=None
        user = fx.make_user(db, tenant)
        db.commit()

        enforce_plan_limit(db, tenant, "mesas")
        require_module_access("inventario")(tenant=tenant, db=db, _user=user)

    # ---------------------------------------------------------- Acceptance Scenario 5

    def test_renovar_levanta_el_bloqueo_de_inmediato(self):
        db = fx.new_session()
        tenant = _vencido_tenant(db)
        db.commit()

        with self.assertRaises(HTTPException):
            enforce_plan_limit(db, tenant, "mesas")

        # Renovar es el mismo PATCH que US2 ya prueba — aquí solo se simula
        # su efecto de datos (reiniciar plan_iniciado_en/plan_vence_en),
        # que es lo único que ensure_plan_not_expired consulta.
        from app.core.plan_limits import calculate_plan_vencimiento

        tenant.plan_iniciado_en = utc_now().replace(tzinfo=None)
        tenant.plan_vence_en = calculate_plan_vencimiento(tenant.plan_iniciado_en, "mensual")
        db.commit()

        enforce_plan_limit(db, tenant, "mesas")  # ya no bloquea


if __name__ == "__main__":
    unittest.main()
