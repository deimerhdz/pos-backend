"""Tests de la nueva funcionalidad — spec 088-integridad-referencias-r2 (A-92), imagen (QR) de un
método de pago (`sales/service.py`: `create_payment_method` / `update_payment_method`).

Algoritmo por clave `format:"image"` de research D7:
- US1: sin `payment_info_base` o con `base[k]` distinto de la vigente (formulario desactualizado)
  se conserva el valor vigente de `k` y las demás claves se guardan; una edición legítima con
  `k` vacío/ausente lo elimina (comportamiento actual, el archivo no se borra: D8);
  `is_complete` se recalcula sobre el `payment_info` **resultante**.
- US2: un QR nuevo debe existir en R2 (422); si R2 no responde, 503.
- US3: solo keys del propio negocio y carpeta `payment-methods`.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_payment_methods_qr_integrity -v
"""
import unittest
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import func, select

from app.characterization_tests import payment_catalog_fixtures as fx
from app.api.v1.sales import service
from app.api.v1.sales.schemas import PaymentMethodCreate, PaymentMethodUpdate
from app.core.storage import StorageUnavailable
from app.models.payment import PaymentMethod

SCHEMA = "acme"
CUR = "acme/payment-methods/vigente.png"
NEW = "acme/payment-methods/nuevo.png"
STALE = "acme/payment-methods/viejo.png"
OTHER_ORIGIN = "https://cdn.otro.com/qr.png"
EXISTS = "app.core.asset_refs.object_exists"

_FIELDS = [
    {"key": "celular", "label": "Celular", "required": True, "format": "numeric", "length": 10},
    {"key": "qr", "label": "QR", "required": False, "format": "image"},
]
_FIELDS_QR_REQUIRED = [
    {"key": "celular", "label": "Celular", "required": True, "format": "numeric", "length": 10},
    {"key": "qr", "label": "QR", "required": True, "format": "image"},
]


def _method(db, qr=CUR, fields=_FIELDS, celular="3001234567"):
    catalog = fx.make_payment_method_catalog(db, name="Nequi", type="transfer", fields=fields)
    info = {"celular": celular}
    if qr is not None:
        info["qr"] = qr
    method = fx.make_payment_method(
        db, name="Nequi", type="transfer", is_cash=False, catalog_id=catalog.id, payment_info=info,
    )
    db.commit()
    return method, catalog


def _update(db, method, *, exists=True, **fields):
    with mock.patch(EXISTS, return_value=exists) as exists_mock:
        result = service.update_payment_method(
            db, method.id, PaymentMethodUpdate(**fields), tenant_schema=SCHEMA,
        )
    return result, exists_mock


class TestUS1FormularioDesactualizado(unittest.TestCase):
    def test_sin_base_se_conserva_el_qr_vigente_y_se_guardan_las_demas_claves(self):
        db = fx.new_session()
        method, _ = _method(db)
        result, exists = _update(db, method, payment_info={"celular": "3009999999", "qr": NEW})
        self.assertEqual(result.payment_info, {"celular": "3009999999", "qr": CUR})
        exists.assert_not_called()

    def test_base_distinta_de_la_vigente_conserva_el_qr(self):
        db = fx.new_session()
        method, _ = _method(db)
        result, exists = _update(
            db, method,
            payment_info={"celular": "3009999999", "qr": NEW},
            payment_info_base={"celular": "3001234567", "qr": STALE},
        )
        self.assertEqual(result.payment_info, {"celular": "3009999999", "qr": CUR})
        exists.assert_not_called()

    def test_edicion_legitima_con_qr_nuevo_lo_aplica(self):
        db = fx.new_session()
        method, _ = _method(db)
        result, _ = _update(
            db, method,
            payment_info={"celular": "3001234567", "qr": NEW},
            payment_info_base={"celular": "3001234567", "qr": CUR},
        )
        self.assertEqual(result.payment_info["qr"], NEW)

    def test_edicion_legitima_sin_tocar_el_qr_lo_conserva(self):
        db = fx.new_session()
        method, _ = _method(db)
        result, exists = _update(
            db, method,
            payment_info={"celular": "3009999999", "qr": CUR},
            payment_info_base={"celular": "3001234567", "qr": CUR},
        )
        self.assertEqual(result.payment_info, {"celular": "3009999999", "qr": CUR})
        exists.assert_not_called()

    def test_edicion_legitima_con_qr_vacio_o_ausente_lo_elimina(self):
        # D7 (opción A confirmada 2026-09-29): el QR sí se puede quitar; el archivo no se borra (D8).
        for label, info in (("ausente", {"celular": "3001234567"}), ("vacio", {"celular": "3001234567", "qr": ""})):
            with self.subTest(qr=label):
                db = fx.new_session()
                method, _ = _method(db)
                with mock.patch("app.core.storage.delete_object") as delete:
                    result, exists = _update(
                        db, method, payment_info=info,
                        payment_info_base={"celular": "3001234567", "qr": CUR},
                    )
                self.assertNotIn("qr", {k: v for k, v in result.payment_info.items() if v})
                self.assertEqual(result.payment_info["celular"], "3001234567")
                exists.assert_not_called()
                delete.assert_not_called()

    def test_quitar_el_qr_con_formulario_desactualizado_lo_conserva(self):
        db = fx.new_session()
        method, _ = _method(db)
        result, _ = _update(
            db, method,
            payment_info={"celular": "3009999999"},
            payment_info_base={"celular": "3001234567", "qr": STALE},
        )
        self.assertEqual(result.payment_info, {"celular": "3009999999", "qr": CUR})

    def test_quitar_el_qr_sin_base_lo_conserva(self):
        db = fx.new_session()
        method, _ = _method(db)
        result, _ = _update(db, method, payment_info={"celular": "3009999999"})
        self.assertEqual(result.payment_info, {"celular": "3009999999", "qr": CUR})

    def test_metodo_sin_qr_y_qr_nuevo_con_base_sin_la_clave_lo_aplica(self):
        db = fx.new_session()
        method, _ = _method(db, qr=None)
        result, _ = _update(
            db, method,
            payment_info={"celular": "3001234567", "qr": NEW},
            payment_info_base={"celular": "3001234567"},
        )
        self.assertEqual(result.payment_info["qr"], NEW)

    def test_metodo_sin_qr_y_formulario_desactualizado_no_agrega_la_clave(self):
        db = fx.new_session()
        method, _ = _method(db, qr=None)
        result, _ = _update(db, method, payment_info={"celular": "3001234567", "qr": NEW})
        self.assertEqual(result.payment_info, {"celular": "3001234567"})

    def test_is_complete_se_recalcula_sobre_el_payment_info_resultante(self):
        db = fx.new_session()
        method, _ = _method(db, fields=_FIELDS_QR_REQUIRED)
        self.assertTrue(method.is_complete)
        # Quitar el QR obligatorio con un formulario desactualizado: el QR vigente se conserva,
        # así que el método sigue completo (no se recalcula sobre lo enviado).
        result, _ = _update(db, method, payment_info={"celular": "3001234567"})
        self.assertEqual(result.payment_info["qr"], CUR)
        self.assertTrue(result.is_complete)
        # Quitarlo en una edición legítima sí lo deja incompleto.
        result, _ = _update(
            db, method, payment_info={"celular": "3001234567"},
            payment_info_base={"celular": "3001234567", "qr": CUR},
        )
        self.assertFalse(result.is_complete)

    def test_solo_active_no_toca_el_payment_info(self):
        db = fx.new_session()
        method, _ = _method(db)
        result, exists = _update(db, method, active=True)
        self.assertEqual(result.payment_info, {"celular": "3001234567", "qr": CUR})
        exists.assert_not_called()


class TestUS2Existencia(unittest.TestCase):
    def test_edicion_legitima_con_qr_inexistente_es_422_y_no_cambia_nada(self):
        db = fx.new_session()
        method, _ = _method(db)
        with self.assertRaises(HTTPException) as ctx:
            _update(
                db, method,
                payment_info={"celular": "3009999999", "qr": NEW},
                payment_info_base={"celular": "3001234567", "qr": CUR}, exists=False,
            )
        self.assertEqual(ctx.exception.status_code, 422)
        db.expire_all()
        self.assertEqual(db.get(PaymentMethod, method.id).payment_info, {"celular": "3001234567", "qr": CUR})

    def test_r2_caido_es_503(self):
        db = fx.new_session()
        method, _ = _method(db)
        with mock.patch(EXISTS, side_effect=StorageUnavailable("x")):
            with self.assertRaises(HTTPException) as ctx:
                service.update_payment_method(
                    db, method.id,
                    PaymentMethodUpdate(
                        payment_info={"celular": "3001234567", "qr": NEW},
                        payment_info_base={"celular": "3001234567", "qr": CUR},
                    ),
                    tenant_schema=SCHEMA,
                )
        self.assertEqual(ctx.exception.status_code, 503)
        db.expire_all()
        self.assertEqual(db.get(PaymentMethod, method.id).payment_info["qr"], CUR)

    def test_url_de_otro_origen_no_consulta_r2(self):
        db = fx.new_session()
        method, _ = _method(db)
        result, exists = _update(
            db, method,
            payment_info={"celular": "3001234567", "qr": OTHER_ORIGIN},
            payment_info_base={"celular": "3001234567", "qr": CUR},
        )
        self.assertEqual(result.payment_info["qr"], OTHER_ORIGIN)
        exists.assert_not_called()

    def test_create_con_qr_inexistente_es_422_y_no_crea_la_fila(self):
        db = fx.new_session()
        catalog = fx.make_payment_method_catalog(db, name="Nequi", type="transfer", fields=_FIELDS)
        db.commit()
        with mock.patch(EXISTS, return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                service.create_payment_method(
                    db, PaymentMethodCreate(catalog_id=catalog.id, payment_info={"celular": "3001234567", "qr": NEW}),
                    tenant_schema=SCHEMA,
                )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(db.execute(select(func.count()).select_from(PaymentMethod)).scalar_one(), 0)

    def test_create_con_r2_caido_es_503(self):
        db = fx.new_session()
        catalog = fx.make_payment_method_catalog(db, name="Nequi", type="transfer", fields=_FIELDS)
        db.commit()
        with mock.patch(EXISTS, side_effect=StorageUnavailable("x")):
            with self.assertRaises(HTTPException) as ctx:
                service.create_payment_method(
                    db, PaymentMethodCreate(catalog_id=catalog.id, payment_info={"celular": "3001234567", "qr": NEW}),
                    tenant_schema=SCHEMA,
                )
        self.assertEqual(ctx.exception.status_code, 503)

    def test_create_con_qr_de_otro_origen_o_sin_qr_no_consulta_r2(self):
        db = fx.new_session()
        catalog = fx.make_payment_method_catalog(db, name="Nequi", type="transfer", fields=_FIELDS)
        db.commit()
        with mock.patch(EXISTS) as exists:
            method = service.create_payment_method(
                db, PaymentMethodCreate(catalog_id=catalog.id, payment_info={"celular": "3001234567", "qr": OTHER_ORIGIN}),
                tenant_schema=SCHEMA,
            )
        self.assertEqual(method.payment_info["qr"], OTHER_ORIGIN)
        exists.assert_not_called()

    def test_create_con_qr_existente_lo_guarda(self):
        db = fx.new_session()
        catalog = fx.make_payment_method_catalog(db, name="Nequi", type="transfer", fields=_FIELDS)
        db.commit()
        with mock.patch(EXISTS, return_value=True):
            method = service.create_payment_method(
                db, PaymentMethodCreate(catalog_id=catalog.id, payment_info={"celular": "3001234567", "qr": NEW}),
                tenant_schema=SCHEMA,
            )
        self.assertEqual(method.payment_info["qr"], NEW)


BAD = [
    "globex/payment-methods/x.png",       # otro negocio
    "acme/products/x.png",                # otra carpeta
    "acme/payment-methods/../logo/x.png",
    "acme//payment-methods/x.png",
    "acme\\payment-methods\\x.png",
    "ACME/payment-methods/x.png",
]


class TestUS3AislamientoEntreNegocios(unittest.TestCase):
    def test_create_con_key_ajena_es_422(self):
        for bad in BAD:
            with self.subTest(key=bad):
                db = fx.new_session()
                catalog = fx.make_payment_method_catalog(db, name="Nequi", type="transfer", fields=_FIELDS)
                db.commit()
                with mock.patch(EXISTS, return_value=True):
                    with self.assertRaises(HTTPException) as ctx:
                        service.create_payment_method(
                            db, PaymentMethodCreate(catalog_id=catalog.id, payment_info={"celular": "3001234567", "qr": bad}),
                            tenant_schema=SCHEMA,
                        )
                self.assertEqual(ctx.exception.status_code, 422)
                self.assertEqual(db.execute(select(func.count()).select_from(PaymentMethod)).scalar_one(), 0)

    def test_update_con_key_ajena_es_422_con_y_sin_base(self):
        for bad in BAD:
            for label, base in (("sin base", None), ("base=vigente", {"qr": CUR}), ("base obsoleta", {"qr": STALE})):
                with self.subTest(key=bad, caso=label):
                    db = fx.new_session()
                    method, _ = _method(db)
                    with self.assertRaises(HTTPException) as ctx:
                        _update(
                            db, method, payment_info={"celular": "3009999999", "qr": bad}, payment_info_base=base,
                        )
                    self.assertEqual(ctx.exception.status_code, 422)
                    db.expire_all()
                    self.assertEqual(
                        db.get(PaymentMethod, method.id).payment_info, {"celular": "3001234567", "qr": CUR},
                    )

    def test_qr_historico_fuera_de_convencion_igual_al_vigente_no_da_422(self):
        db = fx.new_session()
        method, _ = _method(db, qr="legacy/qr.png")
        result, _ = _update(
            db, method,
            payment_info={"celular": "3009999999", "qr": "legacy/qr.png"},
            payment_info_base={"celular": "3001234567", "qr": "legacy/qr.png"},
        )
        self.assertEqual(result.payment_info, {"celular": "3009999999", "qr": "legacy/qr.png"})

    def test_sin_esquema_del_negocio_no_se_valida_en_silencio(self):
        # Salvaguarda: un llamador que olvide `tenant_schema` con una imagen de por medio falla
        # de forma explícita en vez de aceptar cualquier key.
        db = fx.new_session()
        method, _ = _method(db)
        with self.assertRaises(ValueError):
            service.update_payment_method(
                db, method.id,
                PaymentMethodUpdate(
                    payment_info={"celular": "3001234567", "qr": NEW}, payment_info_base={"qr": CUR},
                ),
            )


if __name__ == "__main__":
    unittest.main()
