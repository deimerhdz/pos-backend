"""Historia 2 de specs/014-extraccion-motor-catalogo (gate temporal, ARCHIVADO
tras la Historia 3 — ver resultado final abajo): batería comparativa masiva
entre el motor legado (`app.api.v1.catalog.*`) y el paquete extraído
`app.catalog_engine.*`.

No fue un test de negocio nuevo — fue un gate de equivalencia (FR-013): generó
entre 100 y 200 casos deterministas (semilla fija) combinando variantes,
grupos de opciones, recetas y niveles de stock a partir de las mismas
factorías de `app/characterization_tests/fixtures.py` (sin fixture nuevo,
ver research.md Decisión 5), ejecutó ambas implementaciones sobre el MISMO
estado de datos por caso, y fallaba en cuanto un campo divergía.

**Resultado final (T016, previo a la conmutación de la Historia 3):** 150
casos generados con semilla `20260817`, reproducibles byte a byte entre
corridas (T014), **cero divergencias** campo a campo entre `legado` y
`nuevo` (T015) — incluyendo casos que ejercitaron A-02, A-05, A-06, A-32 y
A-33. Ver Edge Case de spec.md: no hubo nada que triar (T017 no aplicó).

**Archivado, no eliminado** (Clarifications, sesión 2026-08-17): tras la
Historia 3, `line_pricing.py`/`consumption_plan.py` son fachadas puras de
`app.catalog_engine` — "legado" y "nuevo" son el mismo código en tiempo de
ejecución, así que comparar ya no tiene sentido. El nombre del fichero
(`catalog_engine_equivalence_gate.py`, sin prefijo `test_`) ya lo excluye de
`python3 -m unittest discover -s app -p "test_*.py"`: no corre como parte de
la red de regresión permanente, que queda cubierta por los 41
characterization tests + el golden master (FR-013). Se conserva como
evidencia histórica de la verificación de equivalencia.
"""
from __future__ import annotations

import json
import random
import unittest
from decimal import Decimal
from typing import Any

from fastapi import HTTPException

from app.characterization_tests import fixtures as f

from app.api.v1.catalog import line_pricing as _legado_pricing
from app.api.v1.catalog import consumption_plan as _legado_consumption
import app.catalog_engine as _nuevo

_SEED = 20260817
_N_CASOS = 150

_LEGADO = {
    "compute_line_price": _legado_pricing.compute_line_price,
    "validate_option_selection": _legado_pricing.validate_option_selection,
    "check_availability": _legado_pricing.check_availability,
    "plan_line_consumption": _legado_consumption.plan_line_consumption,
    "required_consumption": _legado_consumption.required_consumption,
    "ensure_lines_consume_inventory": _legado_consumption.ensure_lines_consume_inventory,
    "grupos_que_descuentan": _legado_pricing.grupos_que_descuentan,
    "group_discounts": _legado_consumption.group_discounts,
}
_NUEVO = {
    "compute_line_price": _nuevo.compute_line_price,
    "validate_option_selection": _nuevo.validate_option_selection,
    "check_availability": _nuevo.check_availability,
    "plan_line_consumption": _nuevo.plan_line_consumption,
    "required_consumption": _nuevo.required_consumption,
    "ensure_lines_consume_inventory": _nuevo.ensure_lines_consume_inventory,
    "grupos_que_descuentan": _nuevo.grupos_que_descuentan,
    "group_discounts": _nuevo.group_discounts,
}


def _try(fn):
    """Ejecuta `fn`, devuelve ('ok', valor) o ('error', {status_code, detail})."""
    try:
        return "ok", fn()
    except HTTPException as exc:
        return "error", {"status_code": exc.status_code, "detail": exc.detail}


def _diff_paths(expected, actual, path=""):
    """Genera rutas tipo 'campo.subcampo' donde `expected` y `actual`
    divergen, con el valor esperado y el observado (mismo estilo que
    `_diff_paths` de `test_golden_master_pricing_consumption.py`)."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual), key=str):
            sub_path = f"{path}.{key}" if path else str(key)
            if key not in expected:
                yield sub_path, "<ausente en legado>", actual[key]
            elif key not in actual:
                yield sub_path, expected[key], "<ausente en nuevo>"
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


# ---------------------------------------------------------------------------
# Generador determinista de especificaciones de caso (sin tocar la base de
# datos: solo valores planos, serializables) — la reproducibilidad depende
# únicamente de la semilla, nunca de UUIDs ni de I/O.
# ---------------------------------------------------------------------------

_PRECIOS = ["0", "1000", "4500.50", "15000", "8999.99"]
_EXTRA_PRECIOS = ["0", "150", "350.25", "1000"]
_CANTIDADES_INSUMO = ["0", "1", "15", "60", "80", "120"]
_CANTIDADES_RECETA = ["1", "15", "60", "80", "120"]  # recipe_items exige quantity > 0 (CHECK constraint)
_STOCKS = ["0", "10", "50", "100", "1000"]


def generar_casos(seed: int, n: int) -> list[dict[str, Any]]:
    """Combina variantes/grupos de opciones/recetas/niveles de stock de forma
    determinista (Decisión 5 de research.md: `random.Random(seed)`, sin
    mutar estado global)."""
    rng = random.Random(seed)
    casos: list[dict[str, Any]] = []
    for i in range(n):
        num_grupos = rng.choice([0, 1, 1, 2])
        num_receta = rng.choice([0, 0, 1, 2])

        receta = [
            {
                "insumo_nombre": f"caso{i}-receta{r}",
                "cantidad": rng.choice(_CANTIDADES_RECETA),
                "stock_inicial": rng.choice(_STOCKS),
            }
            for r in range(num_receta)
        ]

        grupos = []
        for g in range(num_grupos):
            num_opciones = rng.choice([1, 2, 2, 3])
            min_select = rng.choice([0, 0, 1])
            max_select = max(min_select, min(min_select + rng.choice([0, 1, 2]), num_opciones))
            if max_select < 1:
                max_select = 1
            quantity_per_option = rng.choice(["0", "0", "80", "120"])
            opciones = [
                {
                    "nombre": f"caso{i}-grupo{g}-opcion{o}",
                    "extra_price": rng.choice(_EXTRA_PRECIOS),
                    "item_quantity": rng.choice(_CANTIDADES_INSUMO),
                    "con_insumo": rng.choice([True, True, False]),
                    "insumo_nombre": f"caso{i}-grupo{g}-opcion{o}-insumo",
                    "stock_inicial": rng.choice(_STOCKS),
                    "activa": rng.choice([True, True, True, False]),
                }
                for o in range(num_opciones)
            ]
            grupos.append({
                "nombre": f"caso{i}-grupo{g}",
                "min_select": min_select,
                "max_select": max_select,
                "quantity_per_option": quantity_per_option,
                "opciones": opciones,
            })

        seleccion_indices = []
        for g in grupos:
            num_opciones = len(g["opciones"])
            select_count = min(rng.randint(0, num_opciones + 1), num_opciones)
            seleccion_indices.append(sorted(rng.sample(range(num_opciones), select_count)))

        casos.append({
            "caso_id": i,
            "producto_nombre": f"caso{i}-producto",
            "variante_precio": rng.choice(_PRECIOS),
            "cantidad_vendida": rng.randint(1, 5),
            "receta": receta,
            "grupos": grupos,
            "seleccion_indices": seleccion_indices,
            "incluir_opcion_ajena": rng.random() < 0.15,
        })
    return casos


# ---------------------------------------------------------------------------
# Construcción de datos (fixtures.py, sin fixture nuevo) y ejecución de
# ambas implementaciones sobre el mismo estado.
# ---------------------------------------------------------------------------

def _construir_datos(db, caso: dict[str, Any]) -> dict[str, Any]:
    producto = f.make_product(db, name=caso["producto_nombre"])
    variante = f.make_variant(
        db, product=producto, name=f"{caso['producto_nombre']}-variante",
        price=Decimal(caso["variante_precio"]),
    )

    nombres_insumo: dict[Any, str] = {}

    for r in caso["receta"]:
        item = f.make_inventory_item(db, name=r["insumo_nombre"], current_stock=Decimal(r["stock_inicial"]))
        nombres_insumo[item.id] = r["insumo_nombre"]
        f.make_recipe_item(db, variante, item, quantity=Decimal(r["cantidad"]))

    links = []
    grupos_opciones = []
    for g in caso["grupos"]:
        grupo = f.make_option_group(db, name=g["nombre"], min_select=g["min_select"], max_select=g["max_select"])
        link = f.link_variant_group(
            db, variante, grupo,
            min_select=g["min_select"], max_select=g["max_select"],
            quantity_per_option=Decimal(g["quantity_per_option"]),
        )
        links.append(link)
        opciones = []
        for o in g["opciones"]:
            insumo_id = None
            if o["con_insumo"]:
                item = f.make_inventory_item(db, name=o["insumo_nombre"], current_stock=Decimal(o["stock_inicial"]))
                nombres_insumo[item.id] = o["insumo_nombre"]
                insumo_id = item.id
            opcion = f.make_option(
                db, group=grupo, name=o["nombre"],
                extra_price=Decimal(o["extra_price"]), item_quantity=Decimal(o["item_quantity"]),
                inventory_item_id=insumo_id, active=o["activa"],
            )
            opciones.append(opcion)
        grupos_opciones.append(opciones)

    elegidas = [
        opciones[idx]
        for opciones, idxs in zip(grupos_opciones, caso["seleccion_indices"])
        for idx in idxs
    ]

    if caso["incluir_opcion_ajena"]:
        # A-06: opción de un grupo que la variante no ofrece en absoluto.
        grupo_ajeno = f.make_option_group(db, name=f"{caso['producto_nombre']}-grupo-ajeno", min_select=0, max_select=1)
        item_ajeno = f.make_inventory_item(db, name=f"{caso['producto_nombre']}-insumo-ajeno", current_stock=Decimal("1000"))
        nombres_insumo[item_ajeno.id] = f"{caso['producto_nombre']}-insumo-ajeno"
        opcion_ajena = f.make_option(
            db, group=grupo_ajeno, name=f"{caso['producto_nombre']}-opcion-ajena",
            extra_price=Decimal("100"), item_quantity=Decimal("5"), inventory_item_id=item_ajeno.id,
        )
        elegidas.append(opcion_ajena)

    return {"variante": variante, "elegidas": elegidas, "links": links, "nombres_insumo": nombres_insumo}


def _ejecutar_implementacion(db, objetos: dict[str, Any], caso: dict[str, Any], impl: dict[str, Any]) -> dict[str, Any]:
    variante = objetos["variante"]
    elegidas = objetos["elegidas"]
    cantidad = caso["cantidad_vendida"]
    nombres_insumo = objetos["nombres_insumo"]

    precio = impl["compute_line_price"](variante, elegidas)
    v_status, v_val = _try(lambda: impl["validate_option_selection"](db, variante, elegidas))
    lineas = impl["plan_line_consumption"](db, variante.id, cantidad, elegidas)
    req = impl["required_consumption"](db, variante.id, cantidad, elegidas)
    e_status, e_val = _try(lambda: impl["ensure_lines_consume_inventory"](db, [(variante.id, cantidad, elegidas)]))
    disp_status, disp_val = _try(lambda: impl["check_availability"](db, req))
    descuentan = impl["grupos_que_descuentan"](db, objetos["links"])
    por_link = [impl["group_discounts"](db, link) for link in objetos["links"]]

    return {
        "precio_linea": precio,
        "validacion_opciones": v_status if v_status == "ok" else v_val,
        "plan_consumo": [
            {
                "insumo": nombres_insumo.get(l.inventory_item_id, str(l.inventory_item_id)),
                "cantidad": l.quantity,
                "origen": l.source,
            }
            for l in lineas
        ],
        "required_consumption": {nombres_insumo.get(k, str(k)): v for k, v in req.items()},
        "ensure_lines_consume_inventory": e_status if e_status == "ok" else e_val,
        "disponibilidad": disp_status if disp_status == "ok" else disp_val,
        "grupos_que_descuentan": descuentan,
        "group_discounts_por_link": por_link,
    }


def ejecutar_caso(caso: dict[str, Any]) -> dict[str, Any]:
    """Construye el estado de datos una sola vez y ejecuta ambas
    implementaciones sobre él (mismo `db`, mismos objetos ORM)."""
    db = f.new_session()
    objetos = _construir_datos(db, caso)
    return {
        "caso_id": caso["caso_id"],
        "entrada": caso,
        "legado": _ejecutar_implementacion(db, objetos, caso, _LEGADO),
        "nuevo": _ejecutar_implementacion(db, objetos, caso, _NUEVO),
    }


class CatalogEngineEquivalenceGateTests(unittest.TestCase):
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
                        f"Caso {caso['caso_id']} diverge entre legado y nuevo "
                        f"en {len(diffs)} campo(s):\n{detalle}"
                    )


if __name__ == "__main__":
    unittest.main()
