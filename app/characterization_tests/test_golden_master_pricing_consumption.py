"""CONGELA (golden master) el núcleo de cálculo del flujo de pedido de mesa
por QR — precio de línea + validación de opciones + plan de consumo de
inventario, encadenados como en la aplicación real.

Ver `golden_master/README.md` para qué es este master, cuándo puede
regenerarse (solo con decisión de negocio documentada) y cómo.
"""
import json
import os
import unittest

from app.characterization_tests.golden_master_core import CASES, build_report

_MASTER_PATH = os.path.join(
    os.path.dirname(__file__), "golden_master", "pricing_consumption.master.json"
)


def _diff_paths(expected, actual, path=""):
    """Genera rutas tipo 'caso.campo.subcampo' donde expected y actual
    divergen, con el valor esperado y el observado, para que el mensaje de
    fallo señale el caso y el campo exactos en vez de un diff genérico."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            sub_path = f"{path}.{key}" if path else key
            if key not in expected:
                yield sub_path, "<ausente en el master>", actual[key]
            elif key not in actual:
                yield sub_path, expected[key], "<ausente en el reporte actual>"
            else:
                yield from _diff_paths(expected[key], actual[key], sub_path)
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            yield f"{path}[len]", len(expected), len(actual)
        for i, (e, a) in enumerate(zip(expected, actual)):
            yield from _diff_paths(e, a, f"{path}[{i}]")
    else:
        if expected != actual:
            yield path, expected, actual


class GoldenMasterPricingConsumptionTests(unittest.TestCase):
    def test_master_file_exists(self):
        self.assertTrue(
            os.path.exists(_MASTER_PATH),
            f"Falta el golden master en {_MASTER_PATH}. Ver golden_master/README.md.",
        )

    def test_todos_los_casos_declarados_coinciden_con_el_master(self):
        with open(_MASTER_PATH, encoding="utf-8") as fh:
            expected = json.load(fh)

        actual = build_report()

        diffs = list(_diff_paths(expected, actual))
        if diffs:
            detalle = "\n".join(f"  {p}: esperado={e!r} actual={a!r}" for p, e, a in diffs)
            self.fail(
                "El comportamiento actual del motor de precio/consumo diverge "
                f"del golden master en {len(diffs)} campo(s):\n{detalle}\n\n"
                "Si este cambio fue autorizado por una decisión de negocio "
                "explícita, ver 'Cuándo se regenera' en golden_master/README.md "
                "antes de actualizar el master. Si no lo fue, es una regresión: "
                "corregir el código, no el master."
            )

    def test_el_master_serializado_es_estable_byte_a_byte(self):
        """El propio formato (orden de claves, indentación) debe ser estable
        para que `git diff` del master sea legible; si esto falla, algo
        cambió en el serializador, no en el negocio."""
        from app.characterization_tests.golden_master_core import report_to_json

        with open(_MASTER_PATH, encoding="utf-8") as fh:
            on_disk = fh.read()
        regenerated = report_to_json(json.loads(on_disk))
        self.assertEqual(on_disk, regenerated)

    def test_los_12_casos_del_master_cubren_las_reglas_y_anomalias_declaradas(self):
        self.assertGreaterEqual(len(CASES), 8)
        self.assertLessEqual(len(CASES), 12)
        with open(_MASTER_PATH, encoding="utf-8") as fh:
            expected = json.load(fh)
        self.assertEqual(set(name for name, _ in CASES), set(expected.keys()))


if __name__ == "__main__":
    unittest.main()
