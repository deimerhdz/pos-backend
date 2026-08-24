"""Tests de la nueva funcionalidad — spec 032-catalogo-metodos-pago, Historia de
Usuario 1: el Super Admin administra el catálogo de métodos de pago de la
plataforma (FR-001/FR-002/FR-003/FR-004/FR-014).

No son characterization tests ("CONGELA comportamiento actual"): el catálogo
de plataforma es comportamiento enteramente nuevo (Constitución, Principio
IV/X) — se verifican contra `spec.md`, no contra un comportamiento heredado.

Invoca las funciones de endpoint directamente como funciones Python (mismo
patrón que `test_cart_router.py`): `Depends(get_current_super_admin)` solo se
resuelve en una request ASGI real, así que aquí se pasa la sesión directamente.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_super_admin_payment_catalog -v
"""
import unittest

from fastapi import HTTPException

from app.characterization_tests import payment_catalog_fixtures as fx
from app.api.v1.super_admin import payment_methods_router as router
from app.api.v1.super_admin.schemas import (
    PaymentMethodCatalogCreate,
    PaymentMethodCatalogUpdate,
    PaymentMethodFieldDefinition,
)
from app.models.payment_method_catalog import PaymentMethodCatalog


class TestSuperAdminPaymentCatalog(unittest.TestCase):
    # ---------------------------------------------------------- Acceptance Scenario 1

    def test_crear_metodo_en_catalogo_queda_activo_y_disponible(self):
        """FR-001/FR-004: el Super Admin crea "Daviplata" con un campo
        obligatorio; queda activo y listado de inmediato."""
        db = fx.new_session()
        created = router.create_payment_method_catalog(
            PaymentMethodCatalogCreate(
                name="Daviplata", type="transfer",
                fields=[PaymentMethodFieldDefinition(
                    key="celular", label="Número de celular", required=True,
                    format="numeric", length=10,
                )],
            ),
            db,
        )
        self.assertTrue(created.active)

        listed = router.list_payment_method_catalog(db)
        self.assertEqual([e.name for e in listed], ["Daviplata"])

    def test_crear_metodo_con_nombre_duplicado_es_409(self):
        db = fx.new_session()
        fx.make_payment_method_catalog(db, name="Nequi")
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            router.create_payment_method_catalog(
                PaymentMethodCatalogCreate(name="Nequi", type="transfer", fields=[]), db,
            )
        self.assertEqual(ctx.exception.status_code, 409)

    # ---------------------------------------------------------- Acceptance Scenario 2

    def test_editar_campos_del_catalogo_no_afecta_configuraciones_ya_completadas(self):
        """Edge case de spec.md: editar `fields` del catálogo no invalida
        `is_complete` de una fila de tenant ya guardada con la definición
        anterior."""
        db = fx.new_session()
        catalog = fx.make_payment_method_catalog(
            db, name="Nequi", type="transfer",
            fields=[{"key": "celular", "label": "Celular", "required": True, "format": "numeric", "length": 10}],
        )
        tenant_method = fx.make_payment_method(
            db, catalog_id=catalog.id, name="Nequi", type="transfer", is_cash=False,
            payment_info={"celular": "3001234567"}, is_complete=True,
        )
        db.commit()

        router.update_payment_method_catalog(
            catalog.id,
            PaymentMethodCatalogUpdate(fields=[
                PaymentMethodFieldDefinition(
                    key="celular", label="Celular", required=True, format="numeric", length=10,
                ),
                PaymentMethodFieldDefinition(
                    key="qr", label="QR", required=True, format="image",
                ),
            ]),
            db,
        )

        db.refresh(tenant_method)
        self.assertTrue(tenant_method.is_complete)

    # ---------------------------------------------------------- Acceptance Scenario 3 y 4

    def test_desactivar_metodo_no_borra_ni_afecta_venta_historica(self):
        """FR-003/FR-013/FR-014: desactivar a nivel plataforma no borra la
        entrada de catálogo ni cambia el método de pago ya registrado en una
        venta histórica."""
        db = fx.new_session()
        catalog = fx.make_payment_method_catalog(db, name="Nequi", type="transfer")
        tenant_method = fx.make_payment_method(
            db, catalog_id=catalog.id, name="Nequi", type="transfer", is_cash=False,
        )
        db.commit()
        historical_method_name = tenant_method.name

        updated = router.update_payment_method_catalog(
            catalog.id, PaymentMethodCatalogUpdate(active=False), db,
        )
        self.assertFalse(updated.active)
        # La entrada sigue existiendo (no se borra).
        self.assertIsNotNone(db.get(PaymentMethodCatalog, catalog.id))
        # El método de pago del tenant (referencia histórica de venta) no cambió.
        db.refresh(tenant_method)
        self.assertEqual(tenant_method.name, historical_method_name)
        self.assertTrue(tenant_method.active)  # FR-013: se apaga la disponibilidad
        # de checkout vía el filtro (US3), no tocando esta fila.


if __name__ == "__main__":
    unittest.main()
