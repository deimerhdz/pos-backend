"""Informe de opciones que ya descuentan inventario por su cuenta.

    python -m app.scripts.opciones_con_consumo_fijo

`options.item_quantity > 0` hace que elegir esa opción descuente su insumo, en la
misma cantidad para todo el catálogo. La cantidad que define cada presentación
(`variant_option_groups.quantity_per_option`) descuenta **además** de eso: el consumo
por opción elegida es `quantity_per_option + option.item_quantity`.

**Correr esto ANTES de configurar cantidades por tamaño.** Si los sabores ya tienen
`item_quantity = 80` porque así se resolvía antes y se añade una cantidad de 60 g en la
presentación pequeña, cada venta descontará 140. La migración no puede adivinar la
intención, así que la decisión es manual: normalmente hay que poner `item_quantity = 0`
en las opciones cuya cantidad pase a definir cada tamaño, y dejarlo solo en los extras
que consumen igual en todos los productos (un topping).

Solo lectura: no modifica nada. Recorre todos los schemas de tenant.
"""
from sqlalchemy import text

from app.core.db import with_db


def _schemas() -> list[str]:
    with with_db(None) as db:
        return [r[0] for r in db.execute(text("SELECT schema FROM shared.tenants")).fetchall()]


def _hay_cantidad_por_variante(schema: str) -> bool:
    """`variant_option_groups` solo existe tras la migración; este informe está pensado
    para correrse también ANTES, así que se degrada en vez de fallar."""
    with with_db(schema) as db:
        return db.execute(
            text("SELECT to_regclass(:q)"), {"q": f"{schema}.variant_option_groups"}
        ).scalar() is not None


def _con_consumo_fijo(schema: str) -> list[dict]:
    """Opciones activas con `item_quantity > 0`, con su grupo, su insumo, si alguna
    presentación ya define una cantidad propia para ese grupo, y dónde se ofrece."""
    tiene_cantidad = (
        f'''EXISTS (SELECT 1 FROM "{schema}".variant_option_groups vog
                    WHERE vog.option_group_id = og.id AND vog.quantity_per_option > 0)'''
        if _hay_cantidad_por_variante(schema) else "false"
    )
    productos = (
        f'''COALESCE((
                SELECT string_agg(DISTINCT p.name, ', ' ORDER BY p.name)
                FROM "{schema}".variant_option_groups vog
                JOIN "{schema}".product_variants pv ON pv.id = vog.product_variant_id
                JOIN "{schema}".products p ON p.id = pv.product_id
                WHERE vog.option_group_id = og.id AND p.active
            ), '(ningún producto)')'''
        if _hay_cantidad_por_variante(schema) else "'(desconocido)'"
    )
    with with_db(schema) as db:
        return [dict(r) for r in db.execute(text(f'''
            SELECT og.name  AS grupo,
                   o.name   AS opcion,
                   o.item_quantity,
                   ii.name  AS insumo,
                   um.abbreviation AS unidad,
                   {tiene_cantidad} AS tiene_slot,
                   {productos} AS productos
            FROM "{schema}".options o
            JOIN "{schema}".option_groups og ON og.id = o.option_group_id
            LEFT JOIN "{schema}".inventory_items ii ON ii.id = o.inventory_item_id
            LEFT JOIN "{schema}".unit_measures um ON um.id = ii.unit_measure_id
            WHERE o.active AND og.active AND o.item_quantity > 0
            ORDER BY og.name, o.name
        ''')).mappings()]


def main() -> int:
    total = 0
    duplicados = 0
    for schema in _schemas():
        try:
            filas = _con_consumo_fijo(schema)
        except Exception as e:
            # Un tenant a medio migrar no debe impedir revisar los demás.
            print(f"\n{schema}: no se pudo revisar ({type(e).__name__}: {e})")
            continue

        if not filas:
            print(f"\n{schema}: ✔ ninguna opción tiene consumo fijo propio")
            continue

        total += len(filas)
        print(f"\n{schema}: {len(filas)} opción(es) con item_quantity > 0")
        for f in filas:
            marca = "⚠ DOBLE" if f["tiene_slot"] else "  "
            if f["tiene_slot"]:
                duplicados += 1
            insumo = f["insumo"] or "(sin insumo — no descuenta)"
            unidad = f" {f['unidad']}" if f["unidad"] else ""
            print(f"  {marca} {f['grupo']} · {f['opcion']}: "
                  f"{f['item_quantity']}{unidad} de {insumo}")
            print(f"          usado en: {f['productos']}")

    print()
    if duplicados:
        print(f"⚠ {duplicados} opción(es) tienen consumo fijo Y pertenecen a un grupo")
        print("  con cantidad propia por presentación: cada venta descuenta las DOS cantidades.")
        print("  Pon item_quantity = 0 en esas opciones si la presentación ya cubre el consumo.")
    elif total:
        print(f"{total} opción(es) con consumo fijo, ninguna en conflicto con una cantidad por tamaño.")
        print("  Revisa si alguna debería pasar a gobernarse por cada presentación")
        print("  (necesario si la cantidad debe variar por producto o tamaño).")
    else:
        print("✔ Nada que reconciliar.")
    return 1 if duplicados else 0


if __name__ == "__main__":
    raise SystemExit(main())
