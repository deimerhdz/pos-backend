"""CONGELA comportamiento corregido: app/api/v1/products/service.py:67-91
(update_product) — cierra la anomalía A-44 (specs/000-reconocimiento/
registro-de-anomalias.md, registro-riesgos.md R23).

Antes de esta corrección (specs/021-correccion-orden-borrado-imagen-r2),
`update_product` borraba el objeto de imagen anterior en Cloudflare R2 ANTES
de `db.commit()`. Si el commit fallaba después por cualquier razón ajena a la
imagen, el `product.image_url` revertía a la URL vieja en memoria/BD, pero el
objeto que esa URL señala ya había sido borrado un paso antes — el producto
quedaba apuntando a una imagen inexistente, sin ningún registro de que
ocurrió (`delete_object` es best-effort: solo loguea, nunca lanza).

Esta spec invierte el orden: `delete_object` se invoca DESPUÉS de un
`db.commit()` exitoso.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_products_service -v
"""
import unittest
from decimal import Decimal
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import select

from app.characterization_tests import fixtures as fx
from app.api.v1.products.service import ProductService
from app.api.v1.products.schemas import ProductCreate, ProductUpdate
from app.api.v1.catalog.schemas import VariantSaveIn, RecipeItemIn, VariantOptionGroupIn
from app.models.product_variant import ProductVariant
from app.models.recipe_item import RecipeItem
from app.models.variant_option_group import VariantOptionGroup

OLD_URL = "https://example.invalid/tenant/products/old.jpg"
NEW_URL = "https://example.invalid/tenant/products/new.jpg"


class TestUpdateProductA44(unittest.TestCase):
    def _seed_product(self, db):
        return fx.make_product(db, image_url=OLD_URL)

    def test_a44_fallo_de_commit_no_deja_referencia_rota(self):
        """FR-002/CA2: si el commit falla, delete_object NUNCA se llama — el
        objeto viejo en R2 y product.image_url quedan consistentes entre sí."""
        db = fx.new_session()
        product = self._seed_product(db)
        db.commit()
        service = ProductService()

        with mock.patch(
            "app.api.v1.products.service.delete_object"
        ) as mock_delete, mock.patch.object(
            db, "commit", side_effect=RuntimeError("fallo ajeno a la imagen")
        ):
            with self.assertRaises(RuntimeError):
                service.update_product(db, fx.make_tenant_stub(), product.id, ProductUpdate(image_url=NEW_URL))

        mock_delete.assert_not_called()

    def test_a44_camino_feliz_borra_despues_del_commit(self):
        """FR-001/SC-001: en éxito, delete_object se invoca después de commit
        — orden nuevo que cierra A-44, sin cambiar el resultado final."""
        db = fx.new_session()
        product = self._seed_product(db)
        db.commit()
        service = ProductService()
        orden = []

        real_commit = db.commit

        with mock.patch(
            "app.api.v1.products.service.delete_object",
            side_effect=lambda key: orden.append("delete"),
        ) as mock_delete, mock.patch.object(
            db, "commit", side_effect=lambda: (orden.append("commit"), real_commit())
        ):
            service.update_product(db, fx.make_tenant_stub(), product.id, ProductUpdate(image_url=NEW_URL))

        self.assertEqual(orden, ["commit", "delete"])
        mock_delete.assert_called_once()

    def test_a44_fallo_de_delete_object_no_revierte_el_cambio_de_imagen(self):
        """FR-004/RN2: delete_object sigue siendo best-effort — un fallo suyo
        (p. ej. R2 no disponible) no revierte el cambio de imagen ya
        persistido. delete_object ya captura toda excepción internamente
        (app/core/storage.py); este test documenta ese contrato, no agrega
        manejo de errores nuevo en update_product."""
        db = fx.new_session()
        product = self._seed_product(db)
        db.commit()
        service = ProductService()

        def _delete_falla(key):
            try:
                raise ConnectionError("R2 no disponible")
            except ConnectionError:
                pass  # delete_object real: logueado, nunca propagado

        with mock.patch(
            "app.api.v1.products.service.delete_object", side_effect=_delete_falla
        ):
            result = service.update_product(db, fx.make_tenant_stub(), product.id, ProductUpdate(image_url=NEW_URL))

        self.assertEqual(result.image_url, NEW_URL)


class TestCreateProductWithVariantTree(unittest.TestCase):
    """Spec 043 (US1, FR-001/FR-006): `POST /products` con `variants` crea el árbol completo
    (presentaciones, receta, grupos de opciones y orden) en una sola transacción."""

    def test_variants_anidadas_crean_presentaciones_receta_y_grupos_en_el_orden_recibido(self):
        db = fx.new_session()
        category = fx.make_category(db)
        item = fx.make_inventory_item(db)
        group = fx.make_option_group(db)
        db.commit()
        service = ProductService()

        data = ProductCreate(
            category_id=category.id,
            name="Cono Waffle",
            preparation_type="prepared",
            variants=[
                VariantSaveIn(
                    name="Pequeño",
                    price=Decimal("8000"),
                    recipe=[RecipeItemIn(inventory_item_id=item.id, quantity=Decimal("0.2"))],
                    option_groups=[
                        VariantOptionGroupIn(option_group_id=group.id, min_select=1, max_select=1)
                    ],
                ),
                VariantSaveIn(name="Grande", price=Decimal("12000")),
            ],
        )
        product = service.create_product(db, fx.make_tenant_stub(), data)

        variants = db.execute(
            select(ProductVariant)
            .where(ProductVariant.product_id == product.id)
            .order_by(ProductVariant.display_order)
        ).scalars().all()
        self.assertEqual([v.name for v in variants], ["Pequeño", "Grande"])
        self.assertEqual([v.display_order for v in variants], [1, 2])

        # `Product.variants` (spec 042, `order_by=ProductVariant.display_order`) es la misma
        # relación que recorre el Menú QR (menu/router.py) -- confirma que el orden asignado por
        # el guardado consolidado también se ve por ese camino, no solo por query directa.
        db.expire(product)
        self.assertEqual([v.name for v in product.variants], ["Pequeño", "Grande"])

        recipe = db.execute(
            select(RecipeItem).where(RecipeItem.product_variant_id == variants[0].id)
        ).scalars().all()
        self.assertEqual(len(recipe), 1)
        self.assertEqual(recipe[0].inventory_item_id, item.id)

        groups = db.execute(
            select(VariantOptionGroup).where(
                VariantOptionGroup.product_variant_id == variants[0].id
            )
        ).scalars().all()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].option_group_id, group.id)

    def test_sin_variants_sigue_creando_single_automatica_back_compat(self):
        """RN-CAT-05: sin `variants` (u omitido), el comportamiento no cambia."""
        db = fx.new_session()
        category = fx.make_category(db)
        db.commit()
        service = ProductService()

        product = service.create_product(
            db, fx.make_tenant_stub(), ProductCreate(category_id=category.id, name="Cono Simple", preparation_type="prepared")
        )

        variants = db.execute(
            select(ProductVariant).where(ProductVariant.product_id == product.id)
        ).scalars().all()
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].name, "Single")
        self.assertEqual(variants[0].price, Decimal("0"))

    def test_respuesta_incluye_variants_con_receta_y_grupos_resueltos(self):
        """FR-006: la respuesta del guardado trae el árbol completo, sin lectura adicional."""
        db = fx.new_session()
        category = fx.make_category(db)
        item = fx.make_inventory_item(db)
        db.commit()
        service = ProductService()

        data = ProductCreate(
            category_id=category.id,
            name="Cono Waffle",
            preparation_type="prepared",
            variants=[
                VariantSaveIn(
                    name="Único",
                    price=Decimal("5000"),
                    recipe=[RecipeItemIn(inventory_item_id=item.id, quantity=Decimal("0.1"))],
                )
            ],
        )
        product = service.create_product(db, fx.make_tenant_stub(), data)
        response = service.to_save_response(product)

        self.assertEqual(len(response.variants), 1)
        self.assertEqual(response.variants[0].name, "Único")
        self.assertEqual(len(response.variants[0].recipe), 1)
        self.assertEqual(response.variants[0].recipe[0].inventory_item_id, item.id)


class TestUpdateProductWithVariantTree(unittest.TestCase):
    """Spec 043 (US2, FR-002): `PATCH /products/{id}` con `variants` reconcilia el conjunto de
    presentaciones activas -- crea, actualiza, desactiva las no listadas -- en una transacción."""

    def test_mezcla_de_crear_actualizar_y_desactivar_reasigna_el_orden_por_posicion(self):
        db = fx.new_session()
        product = fx.make_product(db)
        v1 = fx.make_variant(db, product, name="Pequeña", price=Decimal("1000"))
        v2 = fx.make_variant(db, product, name="Mediana", price=Decimal("2000"))
        db.commit()
        service = ProductService()

        data = ProductUpdate(
            variants=[
                VariantSaveIn(id=v1.id, name="Pequeña", price=Decimal("1500")),  # editar
                VariantSaveIn(name="Grande", price=Decimal("3000")),  # crear
                # v2 ("Mediana") no aparece -> se desactiva
            ]
        )
        service.update_product(db, fx.make_tenant_stub(), product.id, data)
        db.expire_all()

        v1_db = db.get(ProductVariant, v1.id)
        v2_db = db.get(ProductVariant, v2.id)
        self.assertEqual(v1_db.price, Decimal("1500.00"))
        self.assertTrue(v1_db.active)
        self.assertFalse(v2_db.active)

        active = db.execute(
            select(ProductVariant)
            .where(ProductVariant.product_id == product.id, ProductVariant.active.is_(True))
            .order_by(ProductVariant.display_order)
        ).scalars().all()
        self.assertEqual([v.name for v in active], ["Pequeña", "Grande"])
        self.assertEqual([v.display_order for v in active], [1, 2])

    def test_reactivar_una_desactivada_conserva_la_receta_reenviada(self):
        """spec 002 FR-010: reactivar dentro del guardado consolidado conserva la receta que el
        formulario ya cargó y reenvía (no el orden -- ver spec.md Edge Cases)."""
        db = fx.new_session()
        product = fx.make_product(db)
        item = fx.make_inventory_item(db)
        v1 = fx.make_variant(db, product, name="Pequeña", price=Decimal("1000"), active=False)
        fx.make_recipe_item(db, v1, item, quantity=Decimal("0.5"))
        db.commit()
        service = ProductService()

        data = ProductUpdate(
            variants=[
                VariantSaveIn(
                    id=v1.id,
                    name="Pequeña",
                    price=v1.price,
                    recipe=[RecipeItemIn(inventory_item_id=item.id, quantity=Decimal("0.5"))],
                )
            ]
        )
        service.update_product(db, fx.make_tenant_stub(), product.id, data)
        db.expire_all()

        v1_db = db.get(ProductVariant, v1.id)
        self.assertTrue(v1_db.active)
        recipe = db.execute(
            select(RecipeItem).where(RecipeItem.product_variant_id == v1.id)
        ).scalars().all()
        self.assertEqual(len(recipe), 1)
        self.assertEqual(recipe[0].quantity, Decimal("0.500"))

    def test_sin_variants_en_el_body_no_toca_ninguna_presentacion(self):
        """Back-compat: `variants` ausente del body deja las presentaciones intactas."""
        db = fx.new_session()
        product = fx.make_product(db)
        v1 = fx.make_variant(db, product, name="Pequeña", price=Decimal("1000"))
        db.commit()
        service = ProductService()

        service.update_product(db, fx.make_tenant_stub(), product.id, ProductUpdate(name="Nuevo nombre"))
        db.expire_all()

        v1_db = db.get(ProductVariant, v1.id)
        self.assertTrue(v1_db.active)
        self.assertEqual(v1_db.name, "Pequeña")
        self.assertEqual(v1_db.price, Decimal("1000.00"))


class TestConsolidatedSaveAtomicity(unittest.TestCase):
    """Spec 043 (US3, FR-004): cualquier fallo de validación en cualquier parte del árbol aborta
    el guardado completo -- nada se persiste, ni siquiera las partes válidas."""

    def test_nombre_duplicado_en_una_presentacion_no_persiste_ninguna(self):
        db = fx.new_session()
        product = fx.make_product(db)
        existente = fx.make_variant(db, product, name="Pequeña")
        db.commit()
        service = ProductService()

        data = ProductUpdate(
            variants=[
                VariantSaveIn(id=existente.id, name="Pequeña", price=Decimal("1000")),
                VariantSaveIn(name="Mediana", price=Decimal("2000")),
                VariantSaveIn(name="Grande", price=Decimal("3000")),
                VariantSaveIn(name="Pequeña", price=Decimal("4000")),  # choca con `existente`
            ]
        )
        with self.assertRaises(HTTPException) as ctx:
            service.update_product(db, fx.make_tenant_stub(), product.id, data)
        self.assertEqual(ctx.exception.detail["variant_index"], 3)

        db.expire_all()
        variants = db.execute(
            select(ProductVariant).where(ProductVariant.product_id == product.id)
        ).scalars().all()
        # Solo sigue existiendo la presentación original -- ninguna de las tres nuevas del
        # payload se creó, y "Pequeña" no cambió de precio.
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].price, Decimal("0"))

    def test_insumo_repetido_en_receta_no_persiste_nada_del_guardado(self):
        db = fx.new_session()
        product = fx.make_product(db)
        item = fx.make_inventory_item(db)
        db.commit()
        service = ProductService()

        data = ProductCreate(
            category_id=product.category_id,
            name="Producto con receta inválida",
            preparation_type="prepared",
            variants=[
                VariantSaveIn(name="Única", price=Decimal("1000"), recipe=[
                    RecipeItemIn(inventory_item_id=item.id, quantity=Decimal("1")),
                    RecipeItemIn(inventory_item_id=item.id, quantity=Decimal("2")),
                ])
            ],
        )
        with self.assertRaises(HTTPException) as ctx:
            service.create_product(db, fx.make_tenant_stub(), data)
        self.assertEqual(ctx.exception.detail["variant_index"], 0)

        db.expire_all()
        creados = db.execute(
            select(ProductVariant).where(ProductVariant.product_id == product.id)
        ).scalars().all()
        self.assertEqual(len(creados), 0)

    def test_grupo_de_opciones_inactivo_no_persiste_nada_del_guardado(self):
        db = fx.new_session()
        product = fx.make_product(db)
        group = fx.make_option_group(db, active=False)
        v1 = fx.make_variant(db, product, name="Pequeña", price=Decimal("1000"))
        db.commit()
        service = ProductService()

        data = ProductUpdate(
            variants=[
                VariantSaveIn(
                    id=v1.id,
                    name="Pequeña",
                    price=Decimal("9999"),
                    option_groups=[VariantOptionGroupIn(option_group_id=group.id)],
                )
            ]
        )
        with self.assertRaises(HTTPException) as ctx:
            service.update_product(db, fx.make_tenant_stub(), product.id, data)
        self.assertEqual(ctx.exception.detail["variant_index"], 0)

        db.expire_all()
        v1_db = db.get(ProductVariant, v1.id)
        self.assertEqual(v1_db.price, Decimal("1000.00"))
        groups = db.execute(
            select(VariantOptionGroup).where(VariantOptionGroup.product_variant_id == v1.id)
        ).scalars().all()
        self.assertEqual(len(groups), 0)


if __name__ == "__main__":
    unittest.main()
