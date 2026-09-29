"""Tests de la nueva funcionalidad — spec 088-integridad-referencias-r2 (A-92), imagen de
producto (`ProductService.create_product` / `update_product`).

- US1 (FR-001/FR-002): un formulario desactualizado no cambia la imagen vigente y guarda
  el resto de los campos, en silencio y sin borrar ningún archivo.
- US2 (FR-003): una key nueva debe existir en R2 (422) y, si R2 no responde, falla cerrado (503).
- US3 (FR-004/FR-005): solo keys del propio negocio y de la carpeta `products`; nunca se borra
  un archivo que no le pertenece.
- US4 (FR-006/FR-007): el archivo anterior solo se borra, después del commit, si ninguna otra
  fila lo usa; candado por key antes de verificar/borrar.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_products_image_integrity -v
"""
import unittest
from datetime import datetime
from unittest import mock
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from app.characterization_tests import fixtures as fx
from app.api.v1.products.schemas import ProductCreate, ProductUpdate
from app.api.v1.products.service import ProductService
from app.core.storage import StorageUnavailable
from app.models.order_payment_attempt import OrderPaymentAttempt
from app.models.payment import PaymentMethod
from app.models.product import Product

SCHEMA = "acme"
TENANT = fx.make_tenant_stub(schema=SCHEMA)
CUR = "acme/products/vigente.png"
NEW = "acme/products/nueva.png"
STALE = "acme/products/vieja.png"
OTHER_ORIGIN = "https://cdn.otro.com/foto.jpg"

EXISTS = "app.core.asset_refs.object_exists"
DELETE = "app.api.v1.products.service.delete_object"


def _seed(db, image=CUR, **kw):
    product = fx.make_product(db, image_url=image, **kw)
    db.commit()
    return product


def _update(db, product, *, exists=True, **fields):
    """Ejecuta `update_product` con R2 simulado. Devuelve (delete_mock, exists_mock)."""
    with mock.patch(DELETE) as delete, mock.patch(EXISTS, return_value=exists) as exists_mock:
        ProductService().update_product(db, TENANT, product.id, ProductUpdate(**fields))
    return delete, exists_mock


def _reload(db, product):
    db.expire_all()
    return db.get(Product, product.id)


class TestUS1FormularioDesactualizado(unittest.TestCase):
    def test_formulario_desactualizado_conserva_la_imagen_y_guarda_lo_demas(self):
        # El formulario se abrió cuando la imagen era STALE y la reenvía; hoy es CUR.
        db = fx.new_session()
        product = _seed(db, name="Cono")
        delete, exists = _update(
            db, product, name="Cono doble", description="nuevo", image_url=STALE, image_url_base=STALE,
        )
        product = _reload(db, product)
        self.assertEqual(product.name, "Cono doble")
        self.assertEqual(product.description, "nuevo")
        self.assertEqual(product.image_url, CUR)
        delete.assert_not_called()
        exists.assert_not_called()  # IGNORE: ni siquiera se consulta R2

    def test_desactualizado_aunque_el_archivo_aun_exista_conserva_la_vigente(self):
        db = fx.new_session()
        product = _seed(db)
        delete, _ = _update(db, product, name="X", image_url=STALE, image_url_base=STALE, exists=True)
        self.assertEqual(_reload(db, product).image_url, CUR)
        delete.assert_not_called()

    def test_formulario_al_dia_que_sube_imagen_nueva_la_aplica(self):
        db = fx.new_session()
        product = _seed(db)
        delete, _ = _update(db, product, image_url=NEW, image_url_base=CUR)
        self.assertEqual(_reload(db, product).image_url, NEW)
        delete.assert_called_once_with(CUR)

    def test_creacion_con_imagen_y_sin_base_crea_con_esa_imagen(self):
        db = fx.new_session()
        category = fx.make_category(db)
        db.commit()
        with mock.patch(EXISTS, return_value=True):
            product = ProductService().create_product(
                db, TENANT, ProductCreate(category_id=category.id, name="Nuevo", image_url=NEW),
            )
        self.assertEqual(product.image_url, NEW)

    def test_formulario_al_dia_sin_tocar_la_imagen_no_cambia_nada(self):
        db = fx.new_session()
        product = _seed(db)
        delete, exists = _update(db, product, name="Solo nombre")  # sin image_url
        self.assertEqual(_reload(db, product).image_url, CUR)
        delete.assert_not_called()
        exists.assert_not_called()

    def test_formulario_que_reenvia_la_imagen_vigente_no_cambia_nada(self):
        db = fx.new_session()
        product = _seed(db)
        delete, exists = _update(db, product, name="Solo nombre", image_url=CUR, image_url_base=CUR)
        self.assertEqual(_reload(db, product).image_url, CUR)
        delete.assert_not_called()
        exists.assert_not_called()

    def test_respuesta_exitosa_y_sin_aviso_con_formulario_desactualizado(self):
        db = fx.new_session()
        product = _seed(db)
        with mock.patch(DELETE), mock.patch(EXISTS, return_value=True):
            result = ProductService().update_product(
                db, TENANT, product.id, ProductUpdate(name="Z", image_url=STALE, image_url_base=STALE),
            )
        self.assertEqual(result.name, "Z")
        self.assertEqual(result.image_url, CUR)

    def test_sin_base_y_key_valida_distinta_se_ignora(self):
        db = fx.new_session()
        product = _seed(db)
        delete, exists = _update(db, product, name="Y", image_url=NEW)  # sin image_url_base
        product = _reload(db, product)
        self.assertEqual((product.name, product.image_url), ("Y", CUR))
        delete.assert_not_called()
        exists.assert_not_called()

    def test_base_null_explicito_sobre_producto_sin_imagen_aplica_la_primera_imagen(self):
        # "No enviada" != null (research D5).
        db = fx.new_session()
        product = _seed(db, image=None)
        _update(db, product, image_url=NEW, image_url_base=None)
        self.assertEqual(_reload(db, product).image_url, NEW)

    def test_sin_base_sobre_producto_sin_imagen_se_ignora(self):
        db = fx.new_session()
        product = _seed(db, image=None)
        _update(db, product, name="Y", image_url=NEW)
        self.assertIsNone(_reload(db, product).image_url)

    def test_key_ajena_con_formulario_desactualizado_es_422_y_no_guarda_nada(self):
        db = fx.new_session()
        product = _seed(db, name="Original")
        with mock.patch(DELETE) as delete, mock.patch(EXISTS, return_value=True):
            with self.assertRaises(HTTPException) as ctx:
                ProductService().update_product(
                    db, TENANT, product.id,
                    ProductUpdate(name="Cambiado", image_url="globex/products/zzz.jpg", image_url_base=STALE),
                )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail, "La imagen no es válida para este negocio.")
        product = _reload(db, product)
        self.assertEqual((product.name, product.image_url), ("Original", CUR))
        delete.assert_not_called()


class TestUS2Existencia(unittest.TestCase):
    def test_edicion_legitima_con_archivo_inexistente_es_422_y_no_cambia_nada(self):
        db = fx.new_session()
        product = _seed(db, name="Original")
        with mock.patch(DELETE) as delete, mock.patch(EXISTS, return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                ProductService().update_product(
                    db, TENANT, product.id, ProductUpdate(name="Cambiado", image_url=NEW, image_url_base=CUR),
                )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(
            ctx.exception.detail,
            "La imagen no se encontró en el almacenamiento. Sube el archivo de nuevo.",
        )
        product = _reload(db, product)
        self.assertEqual((product.name, product.image_url), ("Original", CUR))
        delete.assert_not_called()

    def test_almacenamiento_caido_es_503_sin_cambios_ni_borrado(self):
        db = fx.new_session()
        product = _seed(db, name="Original")
        with mock.patch(DELETE) as delete, mock.patch(EXISTS, side_effect=StorageUnavailable("x")):
            with self.assertRaises(HTTPException) as ctx:
                ProductService().update_product(
                    db, TENANT, product.id, ProductUpdate(name="Cambiado", image_url=NEW, image_url_base=CUR),
                )
        self.assertEqual(ctx.exception.status_code, 503)
        product = _reload(db, product)
        self.assertEqual((product.name, product.image_url), ("Original", CUR))
        delete.assert_not_called()

    def test_valor_vacio_es_keep_sin_llamar_a_r2(self):
        for empty in (None, "", "  "):
            with self.subTest(image_url=empty):
                db = fx.new_session()
                product = _seed(db)
                delete, exists = _update(db, product, name="Y", image_url=empty, image_url_base=CUR)
                self.assertEqual(_reload(db, product).image_url, CUR)
                exists.assert_not_called()
                delete.assert_not_called()

    def test_url_de_otro_origen_no_consulta_r2(self):
        db = fx.new_session()
        product = _seed(db)
        delete, exists = _update(db, product, image_url=OTHER_ORIGIN, image_url_base=CUR)
        self.assertEqual(_reload(db, product).image_url, OTHER_ORIGIN)
        exists.assert_not_called()
        delete.assert_called_once_with(CUR)  # la vigente era del negocio y nadie más la usa

    def test_desactualizado_con_archivo_inexistente_se_ignora_sin_error_ni_llamar_a_r2(self):
        # Resumen (a) de FR-002.
        db = fx.new_session()
        product = _seed(db)
        delete, exists = _update(db, product, name="Y", image_url=NEW, image_url_base=STALE, exists=False)
        self.assertEqual(_reload(db, product).image_url, CUR)
        exists.assert_not_called()
        delete.assert_not_called()

    def test_create_con_imagen_inexistente_es_422_y_no_crea_el_producto(self):
        db = fx.new_session()
        category = fx.make_category(db)
        db.commit()
        with mock.patch(EXISTS, return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                ProductService().create_product(
                    db, TENANT, ProductCreate(category_id=category.id, name="Nuevo", image_url=NEW),
                )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(db.execute(select(func.count()).select_from(Product)).scalar_one(), 0)

    def test_create_con_r2_caido_es_503(self):
        db = fx.new_session()
        category = fx.make_category(db)
        db.commit()
        with mock.patch(EXISTS, side_effect=StorageUnavailable("x")):
            with self.assertRaises(HTTPException) as ctx:
                ProductService().create_product(
                    db, TENANT, ProductCreate(category_id=category.id, name="Nuevo", image_url=NEW),
                )
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(db.execute(select(func.count()).select_from(Product)).scalar_one(), 0)

    def test_create_sin_imagen_o_con_otro_origen_no_consulta_r2(self):
        db = fx.new_session()
        category = fx.make_category(db)
        db.commit()
        with mock.patch(EXISTS) as exists:
            ProductService().create_product(db, TENANT, ProductCreate(category_id=category.id, name="A"))
            ProductService().create_product(
                db, TENANT, ProductCreate(category_id=category.id, name="B", image_url=OTHER_ORIGIN),
            )
        exists.assert_not_called()


BAD_KEYS = [
    "globex/products/zzz.jpg",          # otro negocio
    "acme/logo/x.png",                  # otra carpeta
    "acme/products/../logo/x.png",
    "acme//products/x.jpg",
    "acme\\products\\x.jpg",
    "ACME/products/x.jpg",              # mayúsculas distintas
    "acme/products/x%2e%2e.jpg",
]


class TestUS3AislamientoEntreNegocios(unittest.TestCase):
    def test_keys_ajenas_o_mal_formadas_son_422_sin_borrar_nada(self):
        for bad in BAD_KEYS:
            for label, kwargs in (
                ("base=vigente", dict(image_url_base=CUR)),
                ("sin base", dict()),
                ("base obsoleta", dict(image_url_base=STALE)),
            ):
                with self.subTest(key=bad, caso=label):
                    db = fx.new_session()
                    product = _seed(db, name="Original")
                    with mock.patch(DELETE) as delete, mock.patch(EXISTS, return_value=True):
                        with self.assertRaises(HTTPException) as ctx:
                            ProductService().update_product(
                                db, TENANT, product.id,
                                ProductUpdate(name="Cambiado", image_url=bad, **kwargs),
                            )
                    self.assertEqual(ctx.exception.status_code, 422)
                    product = _reload(db, product)
                    self.assertEqual((product.name, product.image_url), ("Original", CUR))
                    delete.assert_not_called()

    def test_create_con_key_ajena_es_422(self):
        for bad in BAD_KEYS:
            with self.subTest(key=bad):
                db = fx.new_session()
                category = fx.make_category(db)
                db.commit()
                with mock.patch(EXISTS, return_value=True):
                    with self.assertRaises(HTTPException) as ctx:
                        ProductService().create_product(
                            db, TENANT, ProductCreate(category_id=category.id, name="N", image_url=bad),
                        )
                self.assertEqual(ctx.exception.status_code, 422)
                self.assertEqual(db.execute(select(func.count()).select_from(Product)).scalar_one(), 0)

    def test_fila_historica_fuera_de_convencion_se_reemplaza_pero_no_se_borra(self):
        db = fx.new_session()
        product = _seed(db, image="legacy/img/foto.jpg")
        delete, _ = _update(db, product, image_url=NEW, image_url_base="legacy/img/foto.jpg")
        self.assertEqual(_reload(db, product).image_url, NEW)
        delete.assert_not_called()  # queda huérfana (es seguro)

    def test_fila_con_otro_origen_se_reemplaza_y_nunca_se_borra_el_recurso_antiguo(self):
        db = fx.new_session()
        product = _seed(db, image=OTHER_ORIGIN)
        delete, _ = _update(db, product, image_url=NEW, image_url_base=OTHER_ORIGIN)
        self.assertEqual(_reload(db, product).image_url, NEW)
        delete.assert_not_called()

    def test_key_historica_igual_a_la_vigente_reenviada_no_da_422(self):
        db = fx.new_session()
        product = _seed(db, image="legacy/img/foto.jpg")
        delete, _ = _update(
            db, product, name="Y", image_url="legacy/img/foto.jpg", image_url_base="legacy/img/foto.jpg",
        )
        product = _reload(db, product)
        self.assertEqual((product.name, product.image_url), ("Y", "legacy/img/foto.jpg"))
        delete.assert_not_called()

    def test_key_de_otro_negocio_almacenada_no_se_borra(self):
        db = fx.new_session()
        product = _seed(db, image="globex/products/ajena.jpg")
        delete, _ = _update(db, product, image_url=NEW, image_url_base="globex/products/ajena.jpg")
        self.assertEqual(_reload(db, product).image_url, NEW)
        delete.assert_not_called()


def _add_payment_method(db, payment_info):
    method = PaymentMethod(
        id=uuid4(), name=f"metodo-{uuid4()}", type="transfer", is_cash=False, active=True,
        payment_info=payment_info,
    )
    db.add(method)
    db.flush()
    return method


def _add_attempt(db, receipt_file_url):
    attempt = OrderPaymentAttempt(
        id=uuid4(), order_id=uuid4(), payment_method_id=uuid4(), status="pendiente",
        receipt_file_url=receipt_file_url, created_at=datetime.now(),
    )
    db.add(attempt)
    db.flush()
    return attempt


class TestUS4BorradoSoloSiNadieLoUsa(unittest.TestCase):
    def test_dos_productos_con_la_misma_key_el_borrado_solo_ocurre_con_el_ultimo(self):
        db = fx.new_session()
        first = _seed(db, image=CUR, name="Uno")
        second = _seed(db, image=CUR, name="Dos")

        delete, _ = _update(db, first, image_url=NEW, image_url_base=CUR)
        delete.assert_not_called()  # el segundo producto aún usa CUR

        delete, _ = _update(db, second, image_url="acme/products/otra.png", image_url_base=CUR)
        delete.assert_called_once_with(CUR)  # ya nadie la usa

    def test_un_solo_uso_borra_como_siempre(self):
        db = fx.new_session()
        product = _seed(db)
        delete, _ = _update(db, product, image_url=NEW, image_url_base=CUR)
        delete.assert_called_once_with(CUR)  # sin regresión (SC-004)

    def test_referencia_por_logo_impide_el_borrado(self):
        db = fx.new_session()
        fx.make_shared_tenant(db, logo_url=CUR)
        product = _seed(db)
        delete, _ = _update(db, product, image_url=NEW, image_url_base=CUR)
        delete.assert_not_called()

    def test_referencia_por_qr_impide_el_borrado(self):
        db = fx.new_session()
        _add_payment_method(db, {"celular": "3001234567", "qr": CUR})
        product = _seed(db)
        delete, _ = _update(db, product, image_url=NEW, image_url_base=CUR)
        delete.assert_not_called()

    def test_referencia_por_comprobante_impide_el_borrado(self):
        db = fx.new_session()
        _add_attempt(db, CUR)
        product = _seed(db)
        delete, _ = _update(db, product, image_url=NEW, image_url_base=CUR)
        delete.assert_not_called()

    def test_referencia_historica_por_url_absoluta_impide_el_borrado(self):
        db = fx.new_session()
        _add_attempt(db, f"https://example.invalid/{CUR}")
        product = _seed(db)
        delete, _ = _update(db, product, image_url=NEW, image_url_base=CUR)
        delete.assert_not_called()

    def test_fallo_de_delete_object_no_revierte_el_cambio_ni_lanza(self):
        db = fx.new_session()
        product = _seed(db)
        with mock.patch(DELETE, side_effect=RuntimeError("R2 caído")), mock.patch(EXISTS, return_value=True):
            result = ProductService().update_product(
                db, TENANT, product.id, ProductUpdate(image_url=NEW, image_url_base=CUR),
            )
        self.assertEqual(result.image_url, NEW)
        self.assertEqual(_reload(db, product).image_url, NEW)

    def test_dos_actualizaciones_seguidas_borran_solo_la_key_que_cada_una_reemplazo(self):
        db = fx.new_session()
        product = _seed(db)
        delete, _ = _update(db, product, image_url=NEW, image_url_base=CUR)
        delete.assert_called_once_with(CUR)
        delete, _ = _update(db, product, image_url="acme/products/tercera.png", image_url_base=NEW)
        delete.assert_called_once_with(NEW)

    def test_el_chequeo_de_referencias_y_el_borrado_ocurren_despues_del_commit(self):
        db = fx.new_session()
        product = _seed(db)
        events = []
        real_commit = db.commit
        with mock.patch(DELETE, side_effect=lambda key: events.append(f"delete:{key}")), \
             mock.patch(EXISTS, return_value=True), \
             mock.patch("app.core.asset_refs.is_key_referenced",
                        side_effect=lambda _db, key: (events.append(f"check:{key}"), False)[1]), \
             mock.patch.object(db, "commit", side_effect=lambda: (events.append("commit"), real_commit())):
            ProductService().update_product(
                db, TENANT, product.id, ProductUpdate(image_url=NEW, image_url_base=CUR),
            )
        self.assertEqual(events, ["commit", f"check:{CUR}", f"delete:{CUR}"])

    def test_candado_por_key_antes_de_verificar_existencia_y_antes_de_borrar(self):
        db = fx.new_session()
        product = _seed(db)
        events = []
        with mock.patch(DELETE, side_effect=lambda key: events.append(f"delete:{key}")), \
             mock.patch(EXISTS, side_effect=lambda key: (events.append(f"exists:{key}"), True)[1]), \
             mock.patch("app.core.asset_refs.asset_key_lock",
                        side_effect=lambda _db, key: events.append(f"lock:{key}")):
            ProductService().update_product(
                db, TENANT, product.id, ProductUpdate(image_url=NEW, image_url_base=CUR),
            )
        self.assertEqual(
            events, [f"lock:{NEW}", f"exists:{NEW}", f"lock:{CUR}", f"delete:{CUR}"],
        )


if __name__ == "__main__":
    unittest.main()
