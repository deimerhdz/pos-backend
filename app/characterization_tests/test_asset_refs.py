"""Tests de la nueva funcionalidad — spec 088-integridad-referencias-r2, `app/core/asset_refs.py`.

`decide_image_change` (matriz de research D4 / FR-002), `ensure_image_exists` (FR-003),
`is_key_referenced` sobre las cuatro fuentes reales y `asset_key_lock` (FR-006/FR-007),
y `resolve_receipt_key` (FR-008).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_asset_refs -v
"""
import unittest
from datetime import datetime
from unittest import mock
from uuid import uuid4

from fastapi import HTTPException

from app.characterization_tests import fixtures
from app.core import asset_refs
from app.core.asset_refs import (
    ImageDecision,
    asset_key_lock,
    decide_image_change,
    ensure_image_exists,
    is_key_referenced,
    resolve_receipt_key,
)
from app.core.storage import AssetKeyError, StorageUnavailable, normalize_asset_ref
from app.models.order_payment_attempt import OrderPaymentAttempt
from app.models.payment import PaymentMethod

SCHEMA = "acme"
FOLDER = "products"
CURRENT = "acme/products/vigente.png"
NEW = "acme/products/nueva.png"
STALE = "acme/products/vieja.png"
OTHER_ORIGIN = "https://cdn.otro.com/foto.jpg"


def decide(*, sent, current=CURRENT, base=CURRENT, base_provided=True, is_creation=False):
    return decide_image_change(
        tenant_schema=SCHEMA, folder=FOLDER,
        sent=normalize_asset_ref(sent), base_provided=base_provided,
        base=normalize_asset_ref(base), current=normalize_asset_ref(current),
        is_creation=is_creation,
    )


class TestDecideImageChangeEdicion(unittest.TestCase):
    """Las 7 filas de research D4, en edición."""

    def test_1_vacio_es_keep_sin_validar(self):
        for empty in (None, "", "   "):
            with self.subTest(sent=empty):
                self.assertEqual(decide(sent=empty), ImageDecision.KEEP)
                self.assertEqual(decide(sent=empty, base_provided=False), ImageDecision.KEEP)

    def test_2_igual_a_la_vigente_es_keep_aunque_sea_historica(self):
        self.assertEqual(decide(sent=CURRENT), ImageDecision.KEEP)
        # key histórica fuera de convención igual a la vigente: no se valida
        self.assertEqual(
            decide(sent="legacy/img/foto.jpg", current="legacy/img/foto.jpg", base="legacy/img/foto.jpg"),
            ImageDecision.KEEP,
        )
        # una fila con URL absoluta equivale a su key
        self.assertEqual(
            decide(sent="https://assets.example.invalid/acme/products/vigente.png"),
            ImageDecision.KEEP,
        )
        self.assertEqual(
            decide(sent=CURRENT, current="https://example.invalid/acme/products/vigente.png"),
            ImageDecision.KEEP,
        )

    def test_3_gestionada_con_forma_invalida_es_error_siempre(self):
        bad_values = [
            "globex/products/zzz.jpg", "acme/logo/x.png", "acme/products/../logo/x.png",
            "acme//products/x.jpg", "acme\\products\\x.jpg", "ACME/products/x.jpg",
            "acme/products/x%2e%2e.jpg",
        ]
        for bad in bad_values:
            for kwargs in (
                dict(base=CURRENT, base_provided=True),      # formulario al día
                dict(base=None, base_provided=False),        # sin base
                dict(base=STALE, base_provided=True),        # formulario desactualizado
            ):
                with self.subTest(sent=bad, **kwargs):
                    with self.assertRaises(AssetKeyError):
                        decide(sent=bad, **kwargs)

    def test_5_sin_base_o_base_distinta_es_ignore(self):
        self.assertEqual(decide(sent=NEW, base_provided=False, base=None), ImageDecision.IGNORE)
        self.assertEqual(decide(sent=NEW, base=STALE), ImageDecision.IGNORE)
        self.assertEqual(decide(sent=NEW, base=None, base_provided=True), ImageDecision.IGNORE)

    def test_5_ignore_no_toca_r2(self):
        # Resumen (a) de FR-002: key válida distinta + base que no coincide -> IGNORE sin llamar a R2.
        with mock.patch("app.core.storage.object_exists") as exists:
            self.assertEqual(decide(sent=NEW, base=STALE), ImageDecision.IGNORE)
        exists.assert_not_called()

    def test_6_otro_origen_con_base_al_dia_es_apply(self):
        self.assertEqual(decide(sent=OTHER_ORIGIN), ImageDecision.APPLY)

    def test_6_otro_origen_con_base_desactualizada_es_ignore(self):
        self.assertEqual(decide(sent=OTHER_ORIGIN, base=STALE), ImageDecision.IGNORE)
        self.assertEqual(decide(sent=OTHER_ORIGIN, base_provided=False, base=None), ImageDecision.IGNORE)

    def test_7_gestionada_valida_con_base_igual_a_la_vigente_es_apply(self):
        # Resumen (b) de FR-002: base coincide -> APPLY (el llamador verifica existencia).
        self.assertEqual(decide(sent=NEW), ImageDecision.APPLY)
        self.assertEqual(decide(sent="https://assets.example.invalid/acme/products/nueva.png"), ImageDecision.APPLY)

    def test_base_null_explicito_sobre_registro_sin_imagen_es_apply(self):
        # "No enviada" != null (research D5): un producto sin imagen recibe su primera imagen.
        self.assertEqual(decide(sent=NEW, current=None, base=None, base_provided=True), ImageDecision.APPLY)
        self.assertEqual(decide(sent=NEW, current=None, base=None, base_provided=False), ImageDecision.IGNORE)

    def test_base_como_url_absoluta_equivale_a_su_key(self):
        self.assertEqual(
            decide(sent=NEW, base="https://assets.example.invalid/acme/products/vigente.png"),
            ImageDecision.APPLY,
        )


class TestDecideImageChangeCreacion(unittest.TestCase):
    def test_creacion_con_imagen_valida_es_apply_sin_comparar_base(self):
        self.assertEqual(
            decide(sent=NEW, current=None, base=None, base_provided=False, is_creation=True),
            ImageDecision.APPLY,
        )

    def test_creacion_sin_imagen_es_keep(self):
        self.assertEqual(
            decide(sent=None, current=None, base=None, base_provided=False, is_creation=True),
            ImageDecision.KEEP,
        )

    def test_creacion_con_key_invalida_es_error(self):
        with self.assertRaises(AssetKeyError):
            decide(sent="globex/products/x.jpg", current=None, base=None, base_provided=False, is_creation=True)

    def test_creacion_con_otro_origen_es_apply(self):
        self.assertEqual(
            decide(sent=OTHER_ORIGIN, current=None, base=None, base_provided=False, is_creation=True),
            ImageDecision.APPLY,
        )


class TestEnsureImageExists(unittest.TestCase):
    def test_existe(self):
        with mock.patch.object(asset_refs, "object_exists", return_value=True):
            self.assertIsNone(ensure_image_exists(NEW))

    def test_no_existe_es_422(self):
        with mock.patch.object(asset_refs, "object_exists", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                ensure_image_exists(NEW)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(
            ctx.exception.detail,
            "La imagen no se encontró en el almacenamiento. Sube el archivo de nuevo.",
        )

    def test_almacenamiento_caido_es_503(self):
        with mock.patch.object(asset_refs, "object_exists", side_effect=StorageUnavailable("x")):
            with self.assertRaises(HTTPException) as ctx:
                ensure_image_exists(NEW)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(
            ctx.exception.detail,
            "El almacenamiento de archivos no responde. Intenta de nuevo en unos segundos.",
        )


KEY = "acme/products/compartida.png"
ASSETS = "https://assets.example.invalid"
LEGACY = "https://example.invalid"


class TestIsKeyReferenced(unittest.TestCase):
    """Una prueba por cada fuente real (research D9)."""

    def test_sin_referencias_es_false(self):
        db = fixtures.new_session()
        self.assertFalse(is_key_referenced(db, KEY))

    def test_referencia_de_producto(self):
        db = fixtures.new_session()
        fixtures.make_product(db, image_url=KEY)
        self.assertTrue(is_key_referenced(db, KEY))
        self.assertFalse(is_key_referenced(db, "acme/products/otra.png"))

    def test_referencia_en_payment_info_de_un_metodo(self):
        db = fixtures.new_session()
        db.add(PaymentMethod(id=uuid4(), name="Nequi", type="transfer", is_cash=False, active=True,
                             payment_info={"celular": "3001234567", "qr": KEY}))
        db.flush()
        self.assertTrue(is_key_referenced(db, KEY))

    def test_metodo_sin_payment_info_no_falla(self):
        db = fixtures.new_session()
        db.add(PaymentMethod(id=uuid4(), name="Efectivo", type="cash", is_cash=True, active=True,
                             payment_info=None))
        db.flush()
        self.assertFalse(is_key_referenced(db, KEY))

    def test_referencia_de_comprobante(self):
        db = fixtures.new_session()
        db.add(OrderPaymentAttempt(id=uuid4(), order_id=uuid4(), payment_method_id=uuid4(),
                                   status="pendiente", receipt_file_url=KEY, created_at=datetime.now()))
        db.flush()
        self.assertTrue(is_key_referenced(db, KEY))

    def test_referencia_de_logo_de_cualquier_negocio(self):
        db = fixtures.new_session()
        fixtures.make_shared_tenant(db, schema="globex", logo_url=KEY)
        self.assertTrue(is_key_referenced(db, KEY))

    def test_referencia_historica_por_url_absoluta_gestionada(self):
        for label, url in (("dominio anterior", f"{LEGACY}/{KEY}"), ("dominio de assets", f"{ASSETS}/{KEY}")):
            with self.subTest(url=label):
                db = fixtures.new_session()
                fixtures.make_product(db, image_url=url)
                self.assertTrue(is_key_referenced(db, KEY))

    def test_referencia_historica_por_url_absoluta_en_qr_y_comprobante_y_logo(self):
        db = fixtures.new_session()
        db.add(PaymentMethod(id=uuid4(), name="Nequi", type="transfer", is_cash=False, active=True,
                             payment_info={"qr": f"{LEGACY}/{KEY}"}))
        db.flush()
        self.assertTrue(is_key_referenced(db, KEY))
        db = fixtures.new_session()
        db.add(OrderPaymentAttempt(id=uuid4(), order_id=uuid4(), payment_method_id=uuid4(),
                                   status="pendiente", receipt_file_url=f"{ASSETS}/{KEY}",
                                   created_at=datetime.now()))
        db.flush()
        self.assertTrue(is_key_referenced(db, KEY))
        db = fixtures.new_session()
        fixtures.make_shared_tenant(db, logo_url=f"{LEGACY}/{KEY}")
        self.assertTrue(is_key_referenced(db, KEY))

    def test_una_url_de_otro_origen_con_la_misma_cola_no_cuenta(self):
        db = fixtures.new_session()
        fixtures.make_product(db, image_url=f"https://cdn.otro.com/{KEY}")
        self.assertFalse(is_key_referenced(db, KEY))


class TestAssetKeyLock(unittest.TestCase):
    def test_es_no_op_en_sqlite(self):
        db = fixtures.new_session()
        with mock.patch.object(db, "execute") as execute:
            asset_key_lock(db, KEY)
        execute.assert_not_called()

    def test_en_postgresql_toma_el_candado_consultivo_por_key(self):
        db = mock.Mock()
        db.get_bind.return_value.dialect.name = "postgresql"
        asset_key_lock(db, KEY)
        db.execute.assert_called_once()
        statement, params = db.execute.call_args.args
        self.assertIn("pg_advisory_xact_lock", str(statement))
        self.assertIn("hashtextextended", str(statement))
        self.assertEqual(params, {"key": KEY})


RECEIPT = "acme/comprobantes/9c1e4d1c4aa4c68ba7b32642334d084.jpg"
MSG_INVALID = "El comprobante no pertenece a este negocio o no es válido."
MSG_FOREIGN = "El comprobante debe ser un archivo subido desde la aplicación."


class TestResolveReceiptKey(unittest.TestCase):
    def _resolve(self, value, *, exists=True):
        with mock.patch.object(asset_refs, "object_exists", return_value=exists):
            return resolve_receipt_key(value, "acme")

    def _rejected(self, value, status, detail=None, *, exists=True):
        with self.assertRaises(HTTPException, msg=repr(value)) as ctx:
            self._resolve(value, exists=exists)
        self.assertEqual(ctx.exception.status_code, status)
        if detail is not None:
            self.assertEqual(ctx.exception.detail, detail)

    def test_key_valida_existente(self):
        self.assertEqual(self._resolve(RECEIPT), RECEIPT)

    def test_url_del_dominio_publico_anterior_y_del_de_assets_devuelven_la_key(self):
        self.assertEqual(self._resolve(f"{LEGACY}/{RECEIPT}"), RECEIPT)
        self.assertEqual(self._resolve(f"{ASSETS}/{RECEIPT}"), RECEIPT)

    def test_key_mal_formada_ajena_u_otra_carpeta(self):
        for bad in (
            "globex/comprobantes/x.jpg", "acme/products/x.jpg", "acme/comprobantes/../x.jpg",
            "acme//comprobantes/x.jpg", "acme\\comprobantes\\x.jpg", "ACME/comprobantes/x.jpg",
            "acme/comprobantes/", "inventado", f"{ASSETS}/{RECEIPT}?x=1",
        ):
            with self.subTest(value=bad):
                self._rejected(bad, 422, MSG_INVALID)

    def test_url_de_otro_origen(self):
        self._rejected(f"https://cdn.otro.com/{RECEIPT}", 422, MSG_FOREIGN)

    def test_key_valida_pero_inexistente(self):
        self._rejected(
            RECEIPT, 422,
            "La imagen no se encontró en el almacenamiento. Sube el archivo de nuevo.", exists=False,
        )

    def test_r2_no_responde(self):
        with mock.patch.object(asset_refs, "object_exists", side_effect=StorageUnavailable("x")):
            with self.assertRaises(HTTPException) as ctx:
                resolve_receipt_key(RECEIPT, "acme")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_no_consulta_r2_si_la_key_es_invalida(self):
        with mock.patch.object(asset_refs, "object_exists") as exists:
            with self.assertRaises(HTTPException):
                resolve_receipt_key("globex/comprobantes/x.jpg", "acme")
            with self.assertRaises(HTTPException):
                resolve_receipt_key("https://cdn.otro.com/x.jpg", "acme")
        exists.assert_not_called()


if __name__ == "__main__":
    unittest.main()
