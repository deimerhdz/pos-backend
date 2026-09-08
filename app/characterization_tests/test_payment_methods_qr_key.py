"""Tests de la nueva funcionalidad — spec 080-imagenes-key-relativa-r2, US1
para la imagen de un método de pago (`PaymentMethod.payment_info`, clave
marcada `format:"image"`, típ. `qr`).

Al guardar, el valor de la clave de imagen persiste como **key** (forma vieja y
nueva se normalizan, FR-002/FR-004); las claves no-imagen (`celular`, `cuenta`,
`titular`) quedan idénticas. `DinerPaymentMethod` (checkout del comensal) y
`PaymentMethodResponse` (panel admin) devuelven ese valor como URL absoluta
contra `ASSETS_BASE_URL`; el retorno del servicio (ORM) sigue con la key
(FR-006/FR-007).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_payment_methods_qr_key -v
"""
import unittest

from app.characterization_tests import payment_catalog_fixtures as fx
from app.api.v1.sales import service
from app.api.v1.sales.router import payment_method_response
from app.api.v1.sales.schemas import PaymentMethodCreate, PaymentMethodUpdate
from app.api.v1.cart.schemas import DinerPaymentMethod

ASSETS = "https://assets.example.invalid"
LEGACY = "https://example.invalid"
KEY = "heladeria3/payment-methods/46f1a4d1c4aa4c68ba7b32642334d084.png"
URL_LEGACY = f"{LEGACY}/{KEY}"
URL_NEW = f"{ASSETS}/{KEY}"

_FIELDS = [
    {"key": "celular", "label": "Celular", "required": True, "format": "numeric", "length": 10},
    {"key": "qr", "label": "QR", "required": False, "format": "image"},
]


def _nequi_catalog(db):
    return fx.make_payment_method_catalog(db, name="Nequi", type="transfer", fields=_FIELDS)


class TestPaymentInfoPersistsImageAsKey(unittest.TestCase):
    def test_create_normaliza_solo_la_clave_de_imagen(self):
        db = fx.new_session()
        catalog = _nequi_catalog(db)
        db.commit()
        method = service.create_payment_method(db, PaymentMethodCreate(
            catalog_id=catalog.id,
            payment_info={"celular": "3001234567", "qr": URL_LEGACY},
        ))
        self.assertEqual(method.payment_info["qr"], KEY)            # normalizada
        self.assertEqual(method.payment_info["celular"], "3001234567")  # intacta

    def test_create_normaliza_la_forma_del_dominio_nuevo(self):
        db = fx.new_session()
        catalog = _nequi_catalog(db)
        db.commit()
        method = service.create_payment_method(db, PaymentMethodCreate(
            catalog_id=catalog.id, payment_info={"celular": "3001234567", "qr": URL_NEW},
        ))
        self.assertEqual(method.payment_info["qr"], KEY)

    def test_update_normaliza_la_clave_de_imagen(self):
        db = fx.new_session()
        catalog = _nequi_catalog(db)
        method = fx.make_payment_method(
            db, name="Nequi", type="transfer", is_cash=False, catalog_id=catalog.id,
            payment_info={"celular": "3001234567", "qr": KEY},
        )
        db.commit()
        updated = service.update_payment_method(
            db, method.id, PaymentMethodUpdate(payment_info={"celular": "3001234567", "qr": URL_LEGACY}),
        )
        self.assertEqual(updated.payment_info["qr"], KEY)
        self.assertEqual(updated.payment_info["celular"], "3001234567")


class TestPaymentInfoResponseAssembling(unittest.TestCase):
    def test_diner_payment_method_ensambla_la_clave_de_imagen(self):
        dumped = DinerPaymentMethod(
            id=fx._uid(), name="Nequi", type="transfer", is_cash=False,
            payment_info={"celular": "3001234567", "qr": KEY}, fields=_FIELDS,
        ).model_dump()
        self.assertEqual(dumped["payment_info"]["qr"], URL_NEW)
        self.assertEqual(dumped["payment_info"]["celular"], "3001234567")

    def test_diner_payment_method_tolerancia_de_lectura(self):
        dumped = DinerPaymentMethod(
            id=fx._uid(), name="Nequi", type="transfer", is_cash=False,
            payment_info={"qr": URL_LEGACY}, fields=_FIELDS,
        ).model_dump()
        self.assertEqual(dumped["payment_info"]["qr"], URL_NEW)

    def test_payment_method_response_admin_ensambla_la_clave_de_imagen(self):
        db = fx.new_session()
        catalog = _nequi_catalog(db)
        method = fx.make_payment_method(
            db, name="Nequi", type="transfer", is_cash=False, catalog_id=catalog.id,
            payment_info={"celular": "3001234567", "qr": KEY},
        )
        db.commit()
        resp = payment_method_response(method)
        self.assertEqual(resp.payment_info["qr"], URL_NEW)
        self.assertEqual(resp.payment_info["celular"], "3001234567")
        # el ORM sigue con la key (FR-006) — varios characterization de
        # test_sales_payment_methods_catalog lo comparan
        self.assertEqual(method.payment_info["qr"], KEY)

    def test_metodo_sin_payment_info_no_falla(self):
        db = fx.new_session()
        catalog = fx.make_payment_method_catalog(db, name="Efectivo", type="cash")
        method = fx.make_payment_method(db, name="Efectivo", type="cash", catalog_id=catalog.id)
        db.commit()
        resp = payment_method_response(method)
        self.assertIsNone(resp.payment_info)


if __name__ == "__main__":
    unittest.main()
