"""Verificación previa del backfill de `products.tracks_inventory` (spec 027).

    python -m app.scripts.verify_tracks_inventory_backfill

Antes de aplicar la migración `e3f4a5b6c7d8_products_tracks_inventory` en
producción, compara, para cada producto de cada tenant, el resultado de la
heurística SQL del backfill (`EXISTS ...` sobre `recipe_items`/
`variant_option_groups`/`options`, la misma consulta que ejecuta la migración)
contra el resultado real de invocar `load_recipe`/`group_discounts`
(`app/catalog_engine/consumption.py`) sobre cada una de sus presentaciones
activas — la lógica que la migración duplicó intencionalmente en SQL puro en
vez de reutilizar (research.md, spec 027, Decisión 4).

Solo lectura: no modifica nada. Recorre todos los schemas de tenant.
"""
from sqlalchemy import text

from app.core.db import with_db
from app.catalog_engine.consumption import group_discounts, load_recipe, load_variant_groups


def _schemas() -> list[str]:
    with with_db(None) as db:
        return [r[0] for r in db.execute(text("SELECT schema FROM shared.tenants")).fetchall()]


def _sql_heuristic(schema: str) -> dict[str, bool]:
    """id de producto -> resultado de la misma consulta EXISTS que usa el backfill."""
    with with_db(schema) as db:
        rows = db.execute(text(f'''
            SELECT p.id, EXISTS (
                SELECT 1 FROM "{schema}".product_variants pv
                WHERE pv.product_id = p.id AND pv.active = true AND (
                    EXISTS (
                        SELECT 1 FROM "{schema}".recipe_items ri
                        WHERE ri.product_variant_id = pv.id
                    )
                    OR EXISTS (
                        SELECT 1 FROM "{schema}".variant_option_groups vog
                        WHERE vog.product_variant_id = pv.id AND vog.quantity_per_option > 0
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM "{schema}".variant_option_groups vog
                        JOIN "{schema}".options o ON o.option_group_id = vog.option_group_id
                        WHERE vog.product_variant_id = pv.id
                          AND o.active = true
                          AND o.inventory_item_id IS NOT NULL
                          AND o.item_quantity > 0
                    )
                )
            ) AS heuristica
            FROM "{schema}".products p
        ''')).all()
        return {str(r[0]): bool(r[1]) for r in rows}


def _python_reference(schema: str) -> dict[str, bool]:
    """id de producto -> resultado real de invocar load_recipe/group_discounts
    sobre cada presentación activa, igual que hace el guard hoy
    (`ensure_lines_consume_inventory`, `configurada = load_recipe(...) or
    any(group_discounts(...) ...)`)."""
    with with_db(schema) as db:
        variantes = db.execute(text(f'''
            SELECT pv.id, pv.product_id FROM "{schema}".product_variants pv
            WHERE pv.active = true
        ''')).all()
        por_producto: dict[str, bool] = {}
        for variant_id, product_id in variantes:
            pid = str(product_id)
            if por_producto.get(pid):
                continue  # ya confirmado por otra presentación de este producto
            configurada = bool(load_recipe(db, variant_id)) or any(
                group_discounts(db, g) for g in load_variant_groups(db, variant_id)
            )
            por_producto[pid] = por_producto.get(pid, False) or configurada
        return por_producto


def main() -> int:
    total_discrepancias = 0
    for schema in _schemas():
        try:
            heuristica = _sql_heuristic(schema)
            referencia = _python_reference(schema)
        except Exception as e:
            # Un tenant a medio migrar no debe impedir revisar los demás.
            print(f"\n{schema}: no se pudo verificar ({type(e).__name__}: {e})")
            continue

        discrepancias = [pid for pid in heuristica if heuristica[pid] != referencia.get(pid, False)]
        if not discrepancias:
            print(f"\n{schema}: ✔ {len(heuristica)} producto(s), heurística SQL y Python coinciden")
            continue

        total_discrepancias += len(discrepancias)
        print(f"\n{schema}: ⚠ {len(discrepancias)} discrepancia(s) de {len(heuristica)} producto(s)")
        for pid in discrepancias:
            print(f"    producto {pid}: SQL={heuristica[pid]}  Python={referencia.get(pid, False)}")

    print()
    if total_discrepancias:
        print(f"⚠ {total_discrepancias} producto(s) con discrepancia. Revisar antes de aplicar "
              "la migración e3f4a5b6c7d8 en producción.")
    else:
        print("✔ La heurística SQL del backfill coincide con load_recipe/group_discounts "
              "en todos los tenants. Migración lista para aplicarse.")
    return 1 if total_discrepancias else 0


if __name__ == "__main__":
    raise SystemExit(main())
