"""CONGELA app/core/units.py — conversión de unidades de medida.

Referencias: `reglas-de-negocio.md` RN-CAT-40/RN-CAT-41 y
`registro-de-anomalias.md` A-31 (candidato a completar la migración,
"necesitan conversión litros↔onzas para el granizado") del repositorio
`pos-specs`.
"""
import unittest
import uuid
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException

from app.characterization_tests import fixtures as f
from app.core.units import convert
from app.models.unit_measure import UnitMeasure


class ConvertWithDuckTypedUnitsTests(unittest.TestCase):
    """`convert()` solo accede a `.dimension` y `.factor_to_base` por duck
    typing; no exige que el objeto sea un `UnitMeasure` real. Se congela el
    comportamiento aritmético usando objetos mínimos con esos dos atributos,
    porque el modelo real (ver clase siguiente) no los tiene hoy."""

    def test_rn_cat_40_convierte_entre_unidades_de_la_misma_dimension(self):
        kg = SimpleNamespace(abbreviation="kg", dimension="masa", factor_to_base=Decimal("1000"))
        g = SimpleNamespace(abbreviation="g", dimension="masa", factor_to_base=Decimal("1"))
        self.assertEqual(convert(Decimal("2"), kg, g), Decimal("2000"))
        self.assertEqual(convert(Decimal("500"), g, kg), Decimal("0.5"))

    def test_rn_cat_40_dimensiones_distintas_lanza_422(self):
        g = SimpleNamespace(abbreviation="g", dimension="masa", factor_to_base=Decimal("1"))
        ml = SimpleNamespace(abbreviation="ml", dimension="volumen", factor_to_base=Decimal("1"))
        with self.assertRaises(HTTPException) as ctx:
            convert(Decimal("500"), g, ml)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Unidades incompatibles", ctx.exception.detail)
        self.assertIn("g (masa)", ctx.exception.detail)
        self.assertIn("ml (volumen)", ctx.exception.detail)

    def test_convertir_a_la_misma_unidad_es_identidad(self):
        g = SimpleNamespace(abbreviation="g", dimension="masa", factor_to_base=Decimal("1"))
        self.assertEqual(convert(Decimal("123.456"), g, g), Decimal("123.456"))


class ConvertWithRealUnitMeasureModelTests(unittest.TestCase):
    """RN-CAT-41 [HALLAZGO CRÍTICO / CÓDIGO MUERTO]: el modelo real
    `UnitMeasure` (`app/models/unit_measure.py`) NO tiene columnas
    `dimension` ni `factor_to_base`. Invocar `convert()` con instancias
    reales de la BD actual lanza `AttributeError`, no el 422 documentado.
    Se congela ese `AttributeError` tal cual, porque es el comportamiento
    real hoy — no se simula el campo que falta."""

    def setUp(self):
        self.db = f.new_session()

    def test_rn_cat_41_unit_measure_real_no_tiene_dimension_attributeerror(self):
        gramo = f.make_unit(self.db, name="gramo", abbreviation="g")
        mililitro = f.make_unit(self.db, name="mililitro", abbreviation="ml")
        with self.assertRaises(AttributeError):
            convert(Decimal("500"), gramo, mililitro)

    def test_rn_cat_41_unit_measure_no_declara_esas_columnas_en_absoluto(self):
        self.assertNotIn("dimension", UnitMeasure.__table__.columns.keys())
        self.assertNotIn("factor_to_base", UnitMeasure.__table__.columns.keys())


if __name__ == "__main__":
    unittest.main()
