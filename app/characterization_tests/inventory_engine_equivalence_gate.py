"""Historia 2 de specs/018-extraccion-motor-inventario (gate temporal,
ARCHIVADO tras la Historia 3 — ver resultado final abajo): batería
comparativa masiva entre el motor legado (`app.api.v1.inventory.stock`) y el
paquete extraído `app.inventory_engine`.

No fue un test de negocio nuevo — fue un gate de equivalencia (FR-010): generó
150 casos deterministas (semilla `20260817`) combinando tipo de movimiento
(`in`/`out`/rechazado), cantidad, `allow_negative`, `signed_delta`
(positivo/negativo/cero) y niveles de `current_stock` en y cerca de cero, a
partir de las mismas factorías de `app/characterization_tests/fixtures.py`
(sin fixture nuevo, ver research.md Decisión 5), ejecutó ambas
implementaciones sobre insumos con el mismo estado inicial por caso, y
fallaba en cuanto un campo divergía (FR-011).

**Resultado final (T013, previo a la conmutación de la Historia 3):** 150
casos generados con semilla `20260817`, reproducibles byte a byte entre
corridas, **cero divergencias reales** campo a campo entre `legado` y
`nuevo`, incluyendo los tres sub-hallazgos de A-35 en alcance
(`allow_negative=True`, `reason` opcional, `signed_delta=0`). La primera
corrida sí reportó 24 "divergencias" en los mensajes de `InsufficientStockError`,
pero eran un artefacto del propio gate (el nombre autogenerado del insumo de
`fixtures.make_inventory_item` difiere entre el insumo de `legado` y el de
`nuevo` porque son filas distintas) — se corrigió pasando el mismo `item_name`
determinista a ambas implementaciones por caso, no ajustando la aserción para
dejar de detectar nada (FR-011: nunca ajustar el gate para que deje de
detectar una divergencia real).

**Archivado, no eliminado** (mismo tratamiento que
`catalog_engine_equivalence_gate.py` de la spec 014): tras la Historia 3,
`app/api/v1/inventory/stock.py` es una fachada pura de `app.inventory_engine`
— "legado" y "nuevo" son el mismo código en tiempo de ejecución, así que
comparar ya no tiene sentido. El nombre del fichero (sin prefijo `test_`) ya
lo excluye de `python3 -m unittest discover -s app -p "test_*.py"`: no corre
como parte de la red de regresión permanente, que queda cubierta por los 16
characterization tests de `test_inventory_stock.py` (FR-007). Se conserva
como evidencia histórica de la verificación de equivalencia.
"""
from __future__ import annotations

import json
import random
import unittest
import uuid
from decimal import Decimal
from typing import Any, Optional

from app.characterization_tests import fixtures as f

from app.api.v1.inventory import stock as _legado
import app.inventory_engine as _nuevo
from app.core.exceptions import InsufficientStockError

_SEED = 20260817
_N_CASOS = 150

_LEGADO = {
    "record_movement": _legado.record_movement,
    "apply_adjustment": _legado.apply_adjustment,
}
_NUEVO = {
    "record_movement": _nuevo.record_movement,
    "apply_adjustment": _nuevo.apply_adjustment,
}

_STOCKS_INICIALES = ["0", "0.5", "1", "5", "10", "100"]
_CANTIDADES_VALIDAS = ["1", "0.5", "5", "10", "50"]
_CANTIDADES_INVALIDAS = ["0", "-1", "-5.5"]
_TIPOS_VALIDOS = ["in", "out"]
_TIPOS_INVALIDOS = ["adjustment", "invalid"]
_DELTAS = ["1", "0.5", "5", "10", "-1", "-0.5", "-5", "-10"]
_MOTIVOS = [None, "conteo-fisico", "merma"]
_REFERENCE_TYPES = [None, "sale", "order"]


def _try(fn):
    """Ejecuta `fn`, devuelve ('ok', valor) o ('error', {tipo, mensaje})."""
    try:
        return "ok", fn()
    except InsufficientStockError as exc:
        return "error", {"tipo": "InsufficientStockError", "mensaje": exc.detail}
    except ValueError as exc:
        return "error", {"tipo": "ValueError", "mensaje": str(exc)}


def _diff_paths(expected, actual, path=""):
    """Genera rutas tipo 'campo.subcampo' donde `expected` y `actual`
    divergen, con el valor esperado y el observado."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual), key=str):
            sub_path = f"{path}.{key}" if path else str(key)
            if key not in expected:
                yield sub_path, "<ausente en legado>", actual[key]
            elif key not in actual:
                yield sub_path, expected[key], "<ausente en nuevo>"
            else:
                yield from _diff_paths(expected[key], actual[key], sub_path)
    else:
        if expected != actual:
            yield path, expected, actual


# ---------------------------------------------------------------------------
# Generador determinista de especificaciones de caso (solo valores planos,
# serializables) — la reproducibilidad depende únicamente de la semilla.
# ---------------------------------------------------------------------------


def generar_casos(seed: int, n: int) -> list[dict[str, Any]]:
    """Combina tipo de movimiento (in/out/rechazado), cantidad, allow_negative,
    signed_delta (positivo/negativo/cero) y current_stock inicial en y cerca de
    cero, de forma determinista (research.md Decisión 5: `random.Random(seed)`,
    sin mutar estado global)."""
    rng = random.Random(seed)
    casos: list[dict[str, Any]] = []
    for i in range(n):
        funcion = rng.choice(["record_movement", "record_movement", "apply_adjustment"])
        stock_inicial = rng.choice(_STOCKS_INICIALES)
        reason = rng.choice(_MOTIVOS)
        reference_id_presente = rng.choice([True, False])
        user_id_presente = rng.choice([True, False])

        if funcion == "record_movement":
            rama = rng.choice(["valido", "valido", "valido", "cantidad_invalida", "tipo_invalido"])
            allow_negative = rng.choice([True, False, False])
            if rama == "cantidad_invalida":
                entrada = {
                    "funcion": funcion,
                    "type": rng.choice(_TIPOS_VALIDOS),
                    "quantity": rng.choice(_CANTIDADES_INVALIDAS),
                    "reason": reason,
                    "reference_type": rng.choice(_REFERENCE_TYPES),
                    "reference_id_presente": reference_id_presente,
                    "user_id_presente": user_id_presente,
                    "allow_negative": allow_negative,
                    "stock_inicial": stock_inicial,
                }
            elif rama == "tipo_invalido":
                entrada = {
                    "funcion": funcion,
                    "type": rng.choice(_TIPOS_INVALIDOS),
                    "quantity": rng.choice(_CANTIDADES_VALIDAS),
                    "reason": reason,
                    "reference_type": rng.choice(_REFERENCE_TYPES),
                    "reference_id_presente": reference_id_presente,
                    "user_id_presente": user_id_presente,
                    "allow_negative": allow_negative,
                    "stock_inicial": stock_inicial,
                }
            else:
                entrada = {
                    "funcion": funcion,
                    "type": rng.choice(_TIPOS_VALIDOS),
                    "quantity": rng.choice(_CANTIDADES_VALIDAS),
                    "reason": reason,
                    "reference_type": rng.choice(_REFERENCE_TYPES),
                    "reference_id_presente": reference_id_presente,
                    "user_id_presente": user_id_presente,
                    "allow_negative": allow_negative,
                    "stock_inicial": stock_inicial,
                }
        else:
            rama = rng.choice(["distinto_cero", "distinto_cero", "cero"])
            signed_delta = "0" if rama == "cero" else rng.choice(_DELTAS)
            entrada = {
                "funcion": funcion,
                "signed_delta": signed_delta,
                "reason": reason,
                "reference_id_presente": reference_id_presente,
                "user_id_presente": user_id_presente,
                "stock_inicial": stock_inicial,
            }

        casos.append({"caso_id": i, "entrada": entrada})
    return casos


# ---------------------------------------------------------------------------
# Ejecución de ambas implementaciones sobre insumos con el mismo estado
# inicial (sesiones separadas: record_movement/apply_adjustment mutan la fila,
# así que no pueden compartir el mismo insumo entre legado y nuevo).
# ---------------------------------------------------------------------------


def _ejecutar_implementacion(
    entrada: dict[str, Any],
    impl: dict[str, Any],
    reference_id: Optional[uuid.UUID],
    user_id: Optional[uuid.UUID],
    item_name: str,
) -> dict[str, Any]:
    db = f.new_session()
    item = f.make_inventory_item(db, name=item_name, current_stock=Decimal(entrada["stock_inicial"]))

    if entrada["funcion"] == "record_movement":
        status, val = _try(lambda: impl["record_movement"](
            db,
            item.id,
            type=entrada["type"],
            quantity=Decimal(entrada["quantity"]),
            reason=entrada["reason"],
            reference_type=entrada["reference_type"],
            reference_id=reference_id,
            user_id=user_id,
            allow_negative=entrada["allow_negative"],
        ))
    else:
        status, val = _try(lambda: impl["apply_adjustment"](
            db,
            item.id,
            signed_delta=Decimal(entrada["signed_delta"]),
            reason=entrada["reason"],
            user_id=user_id,
        ))

    if status == "error":
        return {"resultado": "error", "error": val}

    movement = val
    return {
        "resultado": "ok",
        "current_stock": str(item.current_stock),
        "movimiento": {
            "type": movement.type,
            "quantity": str(movement.quantity),
            "reason": movement.reason,
            "reference_type": movement.reference_type,
            "reference_id": str(movement.reference_id) if movement.reference_id else None,
            "user_id": str(movement.user_id) if movement.user_id else None,
        },
    }


def ejecutar_caso(caso: dict[str, Any]) -> dict[str, Any]:
    """Genera `reference_id`/`user_id` una sola vez por caso y los pasa
    idénticos a ambas implementaciones, para que la comparación campo a campo
    tenga sentido (cada implementación corre sobre su propio insumo/sesión,
    con el mismo `current_stock` inicial)."""
    entrada = caso["entrada"]
    reference_id = uuid.uuid4() if entrada.get("reference_id_presente") else None
    user_id = uuid.uuid4() if entrada.get("user_id_presente") else None
    item_name = f"insumo-caso-{caso['caso_id']}"
    return {
        "caso_id": caso["caso_id"],
        "entrada": entrada,
        "legado": _ejecutar_implementacion(entrada, _LEGADO, reference_id, user_id, item_name),
        "nuevo": _ejecutar_implementacion(entrada, _NUEVO, reference_id, user_id, item_name),
    }


class InventoryEngineEquivalenceGateTests(unittest.TestCase):
    def test_generador_es_reproducible_entre_corridas_con_la_misma_semilla(self):
        primera = generar_casos(_SEED, _N_CASOS)
        segunda = generar_casos(_SEED, _N_CASOS)
        self.assertEqual(
            json.dumps(primera, sort_keys=True),
            json.dumps(segunda, sort_keys=True),
            "El generador determinista debe producir la misma lista de casos "
            "byte a byte entre dos corridas con la misma semilla.",
        )

    def test_legado_y_nuevo_coinciden_campo_a_campo_en_todos_los_casos(self):
        casos = generar_casos(_SEED, _N_CASOS)
        self.assertGreaterEqual(len(casos), 100)
        self.assertLessEqual(len(casos), 200)

        for caso in casos:
            with self.subTest(caso_id=caso["caso_id"]):
                reporte = ejecutar_caso(caso)
                diffs = list(_diff_paths(reporte["legado"], reporte["nuevo"]))
                if diffs:
                    detalle = "\n".join(f"  {p}: legado={e!r} nuevo={a!r}" for p, e, a in diffs)
                    self.fail(
                        f"Caso {caso['caso_id']} (entrada={reporte['entrada']}) diverge "
                        f"entre legado y nuevo en {len(diffs)} campo(s):\n{detalle}"
                    )


if __name__ == "__main__":
    unittest.main()
