"""Informe de pedidos históricos que violarían la validación de selección de opciones.

    python -m app.scripts.opciones_fuera_de_grupo

`min_select` / `max_select` no se validan hoy en ningún camino de pedido: la única
defensa es el frontend. Mientras la cantidad consumida vivía en la opción eso era un
fallo de UX; con la cantidad por presentación (`variant_option_groups`) deja de ser
determinista, porque N opciones del mismo grupo descuentan N × `quantity_per_option`.

**Correr esto ANTES de activar `STRICT_OPTION_SELECTION`.** Mide tres violaciones
sobre `order_items` ya existentes:

  A. opción de un grupo que esa presentación no ofrece
  B. conteo por grupo fuera de [min_select, max_select]
  C. grupo obligatorio (min_select > 0) sin ninguna opción elegida

Si A y B salen en cero, activar la validación estricta no rompe nada. C suele tener
ruido histórico (grupos vueltos obligatorios después de vender), así que se informa
aparte y no bloquea.

Solo lectura: no modifica nada. Recorre todos los schemas de tenant.
"""
from sqlalchemy import text

from app.core.db import with_db


def _schemas() -> list[str]:
    with with_db(None) as db:
        return [r[0] for r in db.execute(text("SELECT schema FROM shared.tenants")).fetchall()]


def _descuenta(schema: str) -> str:
    """Fragmento SQL: si el grupo tiene cantidad propia en alguna presentación."""
    return '''EXISTS (SELECT 1 FROM "%s".variant_option_groups v2
                     WHERE v2.option_group_id = elegidas.grupo_id
                       AND v2.quantity_per_option > 0)''' % schema


def _grupo_no_asignado(schema: str) -> list[dict]:
    """A: la opción elegida pertenece a un grupo que esa presentación no ofrece."""
    with with_db(schema) as db:
        return [dict(r) for r in db.execute(text(f'''
            SELECT p.name AS producto, pv.name AS variante,
                   og.name AS grupo, o.name AS opcion, count(*) AS veces
            FROM "{schema}".order_item_options oio
            JOIN "{schema}".order_items oi ON oi.id = oio.order_item_id
            JOIN "{schema}".product_variants pv ON pv.id = oi.product_variant_id
            JOIN "{schema}".products p ON p.id = pv.product_id
            JOIN "{schema}".options o ON o.id = oio.option_id
            JOIN "{schema}".option_groups og ON og.id = o.option_group_id
            WHERE NOT EXISTS (
                SELECT 1 FROM "{schema}".variant_option_groups vog
                WHERE vog.product_variant_id = pv.id AND vog.option_group_id = og.id
            )
            GROUP BY p.name, pv.name, og.name, o.name
            ORDER BY count(*) DESC
        ''')).mappings()]


def _cardinalidad(schema: str) -> list[dict]:
    """B: número de opciones elegidas de un grupo fuera de [min_select, max_select]."""
    tiene_slot = _descuenta(schema)
    with with_db(schema) as db:
        return [dict(r) for r in db.execute(text(f'''
            WITH elegidas AS (
                SELECT oi.id AS order_item_id, p.name AS producto, pv.name AS variante,
                       og.id AS grupo_id, og.name AS grupo,
                       pog.min_select, pog.max_select, count(*) AS elegidas
                FROM "{schema}".order_item_options oio
                JOIN "{schema}".order_items oi ON oi.id = oio.order_item_id
                JOIN "{schema}".product_variants pv ON pv.id = oi.product_variant_id
                JOIN "{schema}".products p ON p.id = pv.product_id
                JOIN "{schema}".options o ON o.id = oio.option_id
                JOIN "{schema}".option_groups og ON og.id = o.option_group_id
                JOIN "{schema}".variant_option_groups pog
                     ON pog.product_variant_id = pv.id AND pog.option_group_id = og.id
                GROUP BY oi.id, p.name, pv.name, og.id, og.name, pog.min_select, pog.max_select
            )
            SELECT producto, variante, grupo, min_select, max_select, elegidas,
                   count(*) AS lineas,
                   {tiene_slot} AS tiene_slot
            FROM elegidas
            WHERE elegidas > max_select OR elegidas < min_select
            GROUP BY producto, variante, grupo, grupo_id, min_select, max_select, elegidas
            ORDER BY count(*) DESC
        ''')).mappings()]


def _obligatorio_vacio(schema: str) -> list[dict]:
    """C: grupo con min_select > 0 del que no se eligió nada."""
    with with_db(schema) as db:
        return [dict(r) for r in db.execute(text(f'''
            SELECT p.name AS producto, pv.name AS variante,
                   og.name AS grupo, pog.min_select, count(*) AS lineas
            FROM "{schema}".order_items oi
            JOIN "{schema}".product_variants pv ON pv.id = oi.product_variant_id
            JOIN "{schema}".products p ON p.id = pv.product_id
            JOIN "{schema}".variant_option_groups pog ON pog.product_variant_id = pv.id
            JOIN "{schema}".option_groups og ON og.id = pog.option_group_id
            WHERE pog.min_select > 0
              AND NOT EXISTS (
                  SELECT 1
                  FROM "{schema}".order_item_options oio
                  JOIN "{schema}".options o ON o.id = oio.option_id
                  WHERE oio.order_item_id = oi.id
                    AND o.option_group_id = og.id
              )
            GROUP BY p.name, pv.name, og.name, pog.min_select
            ORDER BY count(*) DESC
        ''')).mappings()]


def main() -> int:
    bloqueantes = 0
    for schema in _schemas():
        print(f"\n=== {schema} ===")
        try:
            no_asignado = _grupo_no_asignado(schema)
            cardinalidad = _cardinalidad(schema)
            obligatorio = _obligatorio_vacio(schema)
        except Exception as e:
            # Un tenant a medio migrar no debe impedir revisar los demás.
            print(f"  no se pudo revisar ({type(e).__name__}: {e})")
            continue

        if no_asignado:
            bloqueantes += len(no_asignado)
            print(f"  A ⚠ {len(no_asignado)} combinación(es) con grupo que la presentación no ofrece")
            for f in no_asignado:
                print(f"      {f['producto']} · {f['variante']} ← {f['grupo']} · {f['opcion']}"
                      f"  ({f['veces']}×)")
        else:
            print("  A ✔ ninguna opción de un grupo no ofrecido")

        if cardinalidad:
            bloqueantes += len(cardinalidad)
            print(f"  B ⚠ {len(cardinalidad)} combinación(es) fuera de min/max_select")
            for f in cardinalidad:
                slot = "  ← YA DESCUENTA" if f["tiene_slot"] else ""
                print(f"      {f['producto']} · {f['variante']} · {f['grupo']}: "
                      f"eligieron {f['elegidas']} (permitido "
                      f"{f['min_select']}–{f['max_select']}) en {f['lineas']} línea(s){slot}")
        else:
            print("  B ✔ toda selección respeta min/max_select")

        if obligatorio:
            print(f"  C · {len(obligatorio)} combinación(es) con grupo obligatorio vacío "
                  f"(informativo, no bloquea)")
            for f in obligatorio:
                print(f"      {f['producto']} · {f['variante']} · {f['grupo']} "
                      f"(min {f['min_select']}) en {f['lineas']} línea(s)")
        else:
            print("  C ✔ ningún grupo obligatorio quedó vacío")

    print()
    if bloqueantes:
        print(f"⚠ {bloqueantes} combinación(es) violan la validación (A o B).")
        print("  Corrige el catálogo antes de activar STRICT_OPTION_SELECTION, o los")
        print("  pedidos que repitan ese patrón empezarán a fallar con 422.")
    else:
        print("✔ Sin violaciones bloqueantes. Se puede activar STRICT_OPTION_SELECTION.")
    return 1 if bloqueantes else 0


if __name__ == "__main__":
    raise SystemExit(main())
