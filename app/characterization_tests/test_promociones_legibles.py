"""spec 066-promociones-legibles-menu — aceptación.

Decisión de negocio **A-66** (`specs/000-reconocimiento/registro-de-anomalias.md`):
el texto de condición de una regla pasa de describir su conjunto por **conteo**
("de estas 8 variantes") a describirlo por **nombres de variante** ("Pequeño 8oz"),
en las tres superficies que leen `condition_text`.

El defecto que origina la spec: en el catálogo real de Springfield los conjuntos son
de una variante por tramo de tamaño, así que el cartel decía tres veces
`"Llevando 2 de estas 1 variantes pagas $X"` — que no le dice al comensal ni qué
producto ni qué tamaño tiene que llevar.

No congela nada: es comportamiento **nuevo**, autorizado por A-66 (Principio IV).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_promociones_legibles -v
"""
from datetime import datetime, time, timezone
from decimal import Decimal
import unittest

from app.characterization_tests import cart_fixtures as fx
from app.api.v1.menu.router import _build_menu, _build_menu_promotions
from app.api.v1.promotions import service

# Dentro de cualquier ventana usada abajo: 18:00 UTC = 13:00 en Bogotá (UTC-5).
DENTRO = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)


def _cartel(db, now=DENTRO):
    """Las líneas del cartel del menú QR, aplanadas: una por regla vigente."""
    return [r.text for a in _build_menu_promotions(db, now) for r in a.rules]


class TestUS1TextoPorNombres(unittest.TestCase):
    """US1 — el comensal entiende qué le ofrece el cartel (FR-001 a FR-006)."""

    def _promo_paquete(self, db, nombres, *, value="12000", min_qty=2, **promo_kw):
        # Cada variante en **su propio producto**: hay un `UNIQUE (product_id,
        # name)`, y el caso real es justamente ese — ocho granizados distintos,
        # cada uno con su tamaño `Pequeño 8oz`.
        variants = [
            fx.make_variant(db, name=n, price=Decimal("8000")) for n in nombres
        ]
        promo = fx.make_promotion(db, name="Semana feliz", status="active", **promo_kw)
        fx.add_rule_to_promotion(
            db, promo, type="package_price", value=Decimal(value), min_qty=min_qty,
            variants=variants,
        )
        db.commit()
        return promo

    def test_ca1_ocho_variantes_con_el_mismo_nombre_se_nombran_una_vez(self):
        """CA1: ocho variantes llamadas todas `Pequeño 8oz` son **un** nombre
        (FR-003), y con un solo nombre no aparece `entre` (FR-004)."""
        db = fx.new_session()
        self._promo_paquete(db, ["Pequeño 8oz"] * 8)

        self.assertEqual(_cartel(db), ["Llevando 2 Pequeño 8oz pagas $12.000"])

    def test_ca2_conjunto_de_una_variante_nunca_dice_de_estas_1_variantes(self):
        """CA2: **el defecto reportado**. Un conjunto de una sola variante se
        nombra; jamás se cuenta."""
        db = fx.new_session()
        self._promo_paquete(db, ["Pequeño 8oz"])

        texto = _cartel(db)[0]
        self.assertEqual(texto, "Llevando 2 Pequeño 8oz pagas $12.000")
        self.assertNotIn("de estas 1 variantes", texto)

    def test_ca3_tres_nombres_en_orden_alfabetico_no_en_el_de_seleccion(self):
        """CA3: el orden lo manda el alfabeto (FR-002), no el orden en que el
        administrador eligió las variantes — por eso `Grande 16oz` va primero."""
        db = fx.new_session()
        self._promo_paquete(
            db, ["Pequeño 8oz", "Mediano 12oz", "Grande 16oz"], value="15000",
        )

        self.assertEqual(
            _cartel(db),
            ["Llevando 2 entre Grande 16oz, Mediano 12oz y Pequeño 8oz pagas $15.000"],
        )

    def test_ca4_cinco_nombres_resume_a_tres_y_cuenta_el_resto(self):
        """CA4: con más de tres nombres se listan los tres primeros del orden y se
        resume el resto; `2 más` cuenta **nombres distintos**, no variantes."""
        db = fx.new_session()
        self._promo_paquete(
            db,
            ["Pequeño 8oz", "Mediano 12oz", "Grande 16oz", "Jumbo 20oz", "Familiar 24oz"],
            value="15000",
        )

        self.assertEqual(
            _cartel(db),
            ["Llevando 2 entre Familiar 24oz, Grande 16oz, Jumbo 20oz y 2 más pagas $15.000"],
        )

    def test_ca5_percent_min_qty_1_no_lleva_entre(self):
        """CA5: `percent` con cantidad mínima 1 es la única de las cuatro formas de
        FR-004 que no lleva `entre`."""
        db = fx.new_session()
        variants = [
            fx.make_variant(db, name="Pequeño 8oz", price=Decimal("8000"))
            for _ in range(8)
        ]
        promo = fx.make_promotion(db, name="10% granizados", status="active")
        fx.add_rule_to_promotion(
            db, promo, type="percent", value=Decimal("10"), min_qty=1, variants=variants,
        )
        db.commit()

        self.assertEqual(_cartel(db), ["10% en Pequeño 8oz"])

    def test_ca6_activa_pero_fuera_de_su_franja_no_anuncia_ninguna_regla(self):
        """CA6: sin cambio respecto de hoy — la vigencia sigue mandando y esta spec
        no la toca (A-57 intacto). 20:00 UTC son las 15:00 en Bogotá, fuera de la
        ventana 20:00-21:00 local."""
        db = fx.new_session()
        self._promo_paquete(
            db, ["Pequeño 8oz"], start_time=time(20, 0), end_time=time(21, 0),
        )

        fuera = datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(_cartel(db, fuera), [])

    def test_sin_ningun_nombre_utilizable_conserva_el_respaldo_por_conteo(self):
        """FR-006: si ninguna variante del conjunto aporta nombre, el texto vuelve
        al descriptor por conteo que existe hoy. Es la única salida que conserva
        `de estas N variantes`."""
        db = fx.new_session()
        # Ni la variante ni su producto aportan nombre. Los blancos son de largo
        # distinto solo para no chocar con el `UNIQUE (product_id, name)`: los tres
        # recortan a vacío, que es lo que la prueba necesita.
        prod = fx.make_product(db, name="   ")
        variants = [
            fx.make_variant(db, product=prod, name=" " * (i + 1), price=Decimal("8000"))
            for i in range(3)
        ]
        promo = fx.make_promotion(db, name="sin nombres", status="active")
        fx.add_rule_to_promotion(
            db, promo, type="percent", value=Decimal("10"), min_qty=1, variants=variants,
        )
        db.commit()

        self.assertEqual(_cartel(db), ["10% en estas 3 variantes"])


class TestUS2InfoPorPresentacion(unittest.TestCase):
    """US2 — el comensal ve el costo real de la presentación (FR-007 a FR-012,
    FR-015). Incluye la corrección de importe de **A-68**: una regla de precio de
    paquete con cantidad mínima 1 pasa a mostrarse como precio vigente, de modo que
    lo mostrado coincida con lo cobrado (SC-003)."""

    def _con_regla(self, db, *, tipo, value, min_qty, price="8000", **promo_kw):
        """Una variante cubierta por una regla vigente. Devuelve la variante."""
        variant = fx.make_variant(db, name="Pequeño 8oz", price=Decimal(price))
        promo = fx.make_promotion(db, name="promo", status="active", **promo_kw)
        fx.add_rule_to_promotion(
            db, promo, type=tipo, value=Decimal(value), min_qty=min_qty,
            variants=[variant],
        )
        db.commit()
        return variant

    def _variante(self, db, variant_id, now=None):
        if now is None:
            menu = _build_menu(db)
        else:
            with fx.frozen_now(now, module="app.api.v1.menu.router"):
                menu = _build_menu(db)
        for cat in menu:
            for prod in cat.products:
                for v in prod.variants:
                    if v.id == variant_id:
                        return v
        return None

    def test_ca1_paquete_min_qty_2_muestra_precio_normal_y_equivalente(self):
        """CA1: $8.000 normal + `2 x $12.000 · $6.000 c/u`. El equivalente es
        exacto, así que **no** lleva `≈`."""
        db = fx.new_session()
        variant = self._con_regla(db, tipo="package_price", value="12000", min_qty=2)

        v = self._variante(db, variant.id)
        self.assertEqual(v.price, Decimal("8000"))
        # `min_qty > 1` no baja el precio unitario: sin carrito no hay grupo.
        self.assertIsNone(v.discounted_price)
        self.assertEqual(v.promotion.short_condition, "2 x $12.000")
        self.assertEqual(v.promotion.unit_equivalent_text, "$6.000 c/u")
        self.assertEqual(v.promotion.display_text, "2 x $12.000 · $6.000 c/u")
        self.assertFalse(v.promotion.unit_equivalent_approx)

    def test_ca3_percent_min_qty_3_equivalente_exacto_sin_aproximado(self):
        """CA3: 15% de $11.000 son $9.350 exactos — sin `≈` (FR-009)."""
        db = fx.new_session()
        variant = self._con_regla(
            db, tipo="percent", value="15", min_qty=3, price="11000",
        )

        v = self._variante(db, variant.id)
        self.assertEqual(v.promotion.display_text, "3 x -15% · $9.350 c/u")
        self.assertFalse(v.promotion.unit_equivalent_approx)
        self.assertIsNone(v.discounted_price)

    def test_equivalente_no_entero_lleva_marca_de_aproximado_en_paquete(self):
        """FR-009: $13.000 entre 3 no da entero -> `≈` y redondeo al peso."""
        db = fx.new_session()
        variant = self._con_regla(
            db, tipo="package_price", value="13000", min_qty=3, price="5000",
        )

        v = self._variante(db, variant.id)
        self.assertTrue(v.promotion.unit_equivalent_approx)
        self.assertEqual(v.promotion.unit_equivalent, Decimal("4333"))
        self.assertEqual(v.promotion.display_text, "3 x $13.000 · ≈ $4.333 c/u")

    def test_equivalente_no_entero_lleva_marca_de_aproximado_en_porcentaje(self):
        """FR-009: la marca aparece **también** en `percent` — 12,5% de $8.700 son
        $7.612,50. El porcentaje se escribe con punto decimal (FR-005)."""
        db = fx.new_session()
        variant = self._con_regla(
            db, tipo="percent", value="12.5", min_qty=2, price="8700",
        )

        v = self._variante(db, variant.id)
        self.assertTrue(v.promotion.unit_equivalent_approx)
        self.assertEqual(v.promotion.display_text, "2 x -12.5% · ≈ $7.613 c/u")

    def test_ca4_paquete_min_qty_1_es_precio_vigente_con_su_tipo_real(self):
        """CA4 + FR-010 (**A-68**, la corrección de importe): el valor de la regla
        pasa a ser el precio vigente, y `discount_kind` lleva el tipo real para que
        el frontend no fabrique un porcentaje que la regla nunca enuncia (D-13)."""
        db = fx.new_session()
        variant = self._con_regla(db, tipo="package_price", value="6000", min_qty=1)

        v = self._variante(db, variant.id)
        self.assertEqual(v.discounted_price, Decimal("6000"))
        self.assertEqual(v.discount_kind, "package_price")
        self.assertEqual(v.promotion.display_text, "1 x $6.000 · $6.000 c/u")

    def test_ca6_percent_min_qty_1_no_cambia_de_importe_y_gana_su_linea(self):
        """CA6 (no-regresión + FR-008): el importe y el tipo son los de producción;
        lo único nuevo es la línea informativa, que ahora también cubre `n = 1`."""
        db = fx.new_session()
        variant = self._con_regla(db, tipo="percent", value="10", min_qty=1)

        v = self._variante(db, variant.id)
        self.assertEqual(v.discounted_price, Decimal("7200.00"))
        self.assertEqual(v.discount_kind, "percent")
        self.assertEqual(v.promotion.display_text, "1 x -10% · $7.200 c/u")

    def test_ca7_solo_la_presentacion_cubierta_lleva_bloque(self):
        """CA7: en un producto de tres presentaciones, las dos que no pertenecen al
        conjunto se ven como siempre."""
        db = fx.new_session()
        prod = fx.make_product(db)
        cubierta = fx.make_variant(db, product=prod, name="Pequeño 8oz", price=Decimal("8000"))
        otras = [
            fx.make_variant(db, product=prod, name=n, price=Decimal("10000"))
            for n in ("Mediano 12oz", "Grande 16oz")
        ]
        promo = fx.make_promotion(db, name="solo pequeños", status="active")
        fx.add_rule_to_promotion(
            db, promo, type="package_price", value=Decimal("12000"), min_qty=2,
            variants=[cubierta],
        )
        db.commit()

        self.assertIsNotNone(self._variante(db, cubierta.id).promotion)
        for otra in otras:
            v = self._variante(db, otra.id)
            self.assertIsNone(v.promotion)
            self.assertIsNone(v.discounted_price)
            self.assertIsNone(v.discount_kind)

    def test_ca8_fuera_de_la_franja_no_hay_bloque_ni_precio_vigente(self):
        """CA8 + tabla de nulidad (contracts/menu-info-promocion.md §5): una
        promoción activa pero fuera de su ventana no puebla nada."""
        db = fx.new_session()
        variant = self._con_regla(
            db, tipo="package_price", value="6000", min_qty=1,
            start_time=time(20, 0), end_time=time(21, 0),
        )

        # 20:00 UTC son las 15:00 en Bogotá: fuera de la ventana 20:00-21:00 local.
        v = self._variante(db, variant.id, now=datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc))
        self.assertIsNone(v.promotion)
        self.assertIsNone(v.discounted_price)
        self.assertIsNone(v.discount_kind)

    def test_sin_ninguna_regla_vigente_no_hay_bloque(self):
        """Tabla de nulidad, primera fila: una variante que ninguna regla cubre."""
        db = fx.new_session()
        variant = fx.make_variant(db, name="Pequeño 8oz", price=Decimal("8000"))
        db.commit()

        v = self._variante(db, variant.id)
        self.assertIsNone(v.promotion)
        self.assertIsNone(v.discounted_price)

    def test_fr015_el_valor_de_la_regla_manda_aunque_supere_el_precio_normal(self):
        """FR-015 + research.md D-6 — el caso que **parece** inalcanzable y no lo es.

        `_guard_package_is_discount` corre en `create`, `update_shape` y
        `change_status`, y **no** cuando el catálogo cambia un precio. Basta con
        bajar el precio de la variante después de activar la promoción para que el
        valor de la regla quede por encima del precio normal.

        Ahí `discounted_price` sigue siendo el valor de la regla: es el importe que
        el cobro aplica, y recortarlo reintroduciría el defecto que A-68 corrige.
        La rama "sin tachado" del frontend depende de este caso."""
        db = fx.new_session()
        variant = self._con_regla(db, tipo="package_price", value="6000", min_qty=1)

        # El catálogo baja de precio **después** de activar: la guarda no corre aquí.
        variant.price = Decimal("5000")
        db.commit()

        v = self._variante(db, variant.id)
        self.assertEqual(v.price, Decimal("5000"))
        self.assertEqual(v.discounted_price, Decimal("6000"))
        self.assertGreater(v.discounted_price, v.price)


class TestSC005MismoTextoEnLasTresSuperficies(unittest.TestCase):
    """SC-005 — el cartel del menú QR, el bloque por presentación y el listado de
    administración devuelven **la misma cadena** para la misma regla.

    Es lo que garantiza que el backend siga siendo la fuente única del texto: las
    tres superficies leen `variant_set_condition_text`, y si alguna se separara
    (por ejemplo, un call site que dejara de pasar los nombres) este test lo
    delata. La cuarta superficie, la vista previa del formulario, no puede consumir
    la API porque describe variantes sin guardar: su equivalencia la protege
    `promotion-condition.util.spec.ts`, con la misma tabla de casos."""

    def test_las_tres_superficies_dicen_exactamente_lo_mismo(self):
        db = fx.new_session()
        variants = [
            fx.make_variant(db, name=n, price=Decimal("8000"))
            for n in ("Pequeño 8oz", "Mediano 12oz", "Grande 16oz")
        ]
        promo = fx.make_promotion(db, name="Semana feliz", status="active")
        fx.add_rule_to_promotion(
            db, promo, type="package_price", value=Decimal("15000"), min_qty=2,
            variants=variants,
        )
        db.commit()

        cartel = _build_menu_promotions(db, DENTRO)[0].rules[0].text
        administracion = service.serialize_promotion(db, promo)["rules"][0]["condition_text"]

        menu = _build_menu(db)
        bloques = [
            v.promotion.condition_text
            for cat in menu for prod in cat.products for v in prod.variants
            if v.promotion is not None
        ]

        esperado = "Llevando 2 entre Grande 16oz, Mediano 12oz y Pequeño 8oz pagas $15.000"
        self.assertEqual(cartel, esperado)
        self.assertEqual(administracion, esperado)
        # Las tres variantes del conjunto describen el conjunto igual: el descriptor
        # es del conjunto, no de la variante desde la que se mira.
        self.assertEqual(len(bloques), 3)
        for bloque in bloques:
            self.assertEqual(bloque, esperado)


if __name__ == "__main__":
    unittest.main()
