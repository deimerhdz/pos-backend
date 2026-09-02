"""Tests de la nueva funcionalidad — spec 033-planes-suscripcion-tenant,
Historia de Usuario 2: el Super Admin asigna, cambia y renueva el plan de un
tenant (FR-003/FR-004/FR-010/FR-011/FR-012/FR-017/FR-018/FR-020).

No son characterization tests — comportamiento enteramente nuevo.

`tenant_create()` (app/core/db.py) exige Postgres real desde su primera
línea (`MigrationContext.configure` contra el motor de Alembic del
proyecto, `sch.CreateSchema`) — no es unit-testeable contra SQLite, ni antes
ni después de esta spec (ningún test existente en este paquete lo intenta).
Por eso Acceptance Scenarios 1 y 5 (creación de tenant) se verifican en dos
capas que sí son unit-testeables: (a) validación Pydantic de
`TenantCreateWithUser` (sin DB), y (b) las funciones que `tenant_create()`
invoca para plan/ciclo (`validate_billing_cycle_price`,
`calculate_plan_vencimiento`, T007) — ya cubiertas ahí. El resto de
Acceptance Scenarios (2-4, 6-7) ejercitan `update_tenant_plan` (el `PATCH`
que cubre asignar/cambiar/renovar) directamente, que no toca ninguna ruta
Postgres-específica.

    python -m unittest app.characterization_tests.test_tenant_plan_assignment -v
"""
import unittest
from datetime import timedelta

from fastapi import HTTPException
from pydantic import ValidationError

from app.characterization_tests import plan_fixtures as fx
from app.api.v1.super_admin import router as super_admin_router
from app.api.v1.super_admin.schemas import TenantCreateWithUser, TenantPlanUpdate
from app.core.plan_limits import enforce_plan_limit, require_module_access
from app.core.timezone import utc_now


class TestTenantCreationRequiresPlanAndCiclo(unittest.TestCase):
    # ---------------------------------------------------------- Acceptance Scenario 1 / 5

    def test_crear_tenant_sin_plan_id_es_invalido(self):
        with self.assertRaises(ValidationError):
            TenantCreateWithUser(
                tenant_name="Heladería Central", schema_name="heladeria_central",
                host="heladeria-central", name="María", email="maria@example.com",
                ciclo_facturacion="mensual",
            )

    def test_crear_tenant_sin_ciclo_facturacion_es_invalido(self):
        """FR-017: el ciclo es obligatorio en el request — `ciclo_facturacion`
        no tiene default (research.md Decisión 15), así que omitirlo (no
        enviarlo como null) también es inválido."""
        with self.assertRaises(ValidationError):
            TenantCreateWithUser(
                tenant_name="Heladería Central", schema_name="heladeria_central",
                host="heladeria-central", name="María", email="maria@example.com",
                plan_id="11111111-1111-1111-1111-111111111111",
            )

    def test_crear_tenant_con_plan_id_y_ciclo_null_es_valido(self):
        """Elegir explícitamente 'sin vencimiento' es una elección válida,
        no un default implícito (FR-021)."""
        body = TenantCreateWithUser(
            tenant_name="Heladería Central", schema_name="heladeria_central",
            host="heladeria-central", name="María", email="maria@example.com",
            plan_id="11111111-1111-1111-1111-111111111111", ciclo_facturacion=None,
        )
        self.assertIsNone(body.ciclo_facturacion)


class TestUpdateTenantPlan(unittest.TestCase):
    # ---------------------------------------------------------- Acceptance Scenario 2 / 5

    def test_cambiar_plan_rige_de_inmediato(self):
        db = fx.new_session()
        basico = fx.make_plan(db, name="Básico", mesas_limit=5)
        pro = fx.make_plan(db, name="Pro", mesas_limit=50, precio_mensual=89000)
        tenant = fx.make_tenant(db, plan=basico)
        db.commit()

        updated = super_admin_router.update_tenant_plan(
            tenant.id, TenantPlanUpdate(plan_id=pro.id, ciclo_facturacion="mensual"), db,
        )
        self.assertEqual(updated.plan_id, pro.id)
        self.assertIsNotNone(updated.plan_vence_en)

    # ---------------------------------------------------------- Acceptance Scenario 3

    def test_bajar_limite_conserva_recursos_existentes_y_bloquea_creaciones_nuevas(self):
        db = fx.new_session()
        plan_amplio = fx.make_plan(db, mesas_limit=8)
        plan_reducido = fx.make_plan(db, mesas_limit=5, precio_mensual=50000)
        tenant = fx.make_tenant(db, plan=plan_amplio)
        for _ in range(8):
            fx.make_dining_table(db)
        db.commit()

        super_admin_router.update_tenant_plan(
            tenant.id, TenantPlanUpdate(plan_id=plan_reducido.id, ciclo_facturacion="mensual"), db,
        )

        # Las 8 mesas ya creadas se conservan — nada las borra.
        from sqlalchemy import func, select
        from app.models.dining_table import DiningTable

        count = db.execute(select(func.count()).select_from(DiningTable)).scalar_one()
        self.assertEqual(count, 8)

        with self.assertRaises(HTTPException) as ctx:
            enforce_plan_limit(db, tenant, "mesas")
        self.assertEqual(ctx.exception.status_code, 403)

    # ---------------------------------------------------------- Acceptance Scenario 4

    def test_quitar_modulo_bloquea_acceso_sin_borrar_datos(self):
        db = fx.new_session()
        con_inventario = fx.make_plan(db, inventario_access=True)
        sin_inventario = fx.make_plan(db, inventario_access=False, precio_mensual=50000)
        tenant = fx.make_tenant(db, plan=con_inventario)
        user = fx.make_user(db, tenant)
        db.commit()

        dep = require_module_access("inventario")
        dep(tenant=tenant, db=db, _user=user)  # permitido antes del cambio

        super_admin_router.update_tenant_plan(
            tenant.id, TenantPlanUpdate(plan_id=sin_inventario.id, ciclo_facturacion="mensual"), db,
        )

        with self.assertRaises(HTTPException) as ctx:
            dep(tenant=tenant, db=db, _user=user)
        self.assertEqual(ctx.exception.status_code, 403)

    # ---------------------------------------------------------- Acceptance Scenario 6

    def test_ciclo_sin_precio_definido_en_el_plan_es_409(self):
        db = fx.new_session()
        plan_sin_precio_anual = fx.make_plan(db, precio_mensual=50000)
        tenant = fx.make_tenant(db, plan=plan_sin_precio_anual)
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            super_admin_router.update_tenant_plan(
                tenant.id, TenantPlanUpdate(plan_id=plan_sin_precio_anual.id, ciclo_facturacion="anual"), db,
            )
        self.assertEqual(ctx.exception.status_code, 409)

    # ---------------------------------------------------------- Acceptance Scenario 7

    def test_renovar_el_mismo_plan_reinicia_el_periodo(self):
        """Renovar es el mismo endpoint que cambiar de plan — mismo plan_id,
        recalcula plan_iniciado_en/plan_vence_en desde ahora (research.md
        Decisión 16)."""
        db = fx.new_session()
        plan = fx.make_plan(db, mesas_limit=5, precio_mensual=50000)
        tenant = fx.make_tenant(
            db, plan=plan, ciclo_facturacion="mensual",
            plan_iniciado_en=utc_now().replace(tzinfo=None) - timedelta(days=40),
        )
        db.commit()
        vencimiento_original = tenant.plan_vence_en

        renewed = super_admin_router.update_tenant_plan(
            tenant.id, TenantPlanUpdate(plan_id=plan.id, ciclo_facturacion="mensual"), db,
        )

        self.assertGreater(renewed.plan_vence_en, vencimiento_original)


if __name__ == "__main__":
    unittest.main()
