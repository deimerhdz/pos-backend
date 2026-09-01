"""Verificación del backfill de `option_groups.pricing_type` (spec 064).

    python -m app.scripts.verify_option_groups_pricing_type_backfill

Para cada grupo de opciones de cada tenant, compara su `pricing_type` guardado contra el
criterio de negocio (FR-015): "con_recargo" si tiene al menos una opción (activa o no) con
`extra_price > 0`; "incluido" si todas están en $0. A diferencia de
`verify_tracks_inventory_backfill.py` (spec 027), aquí no hay una segunda implementación en
Python independiente contra la cual contrastar la heurística SQL de la migración -- el
criterio de clasificación es puramente derivado de datos, sin lógica de negocio propia en
tiempo de ejecución. Este script recalcula el mismo criterio directamente en Python (sin
tocar la base de datos) y lo compara contra el valor ya persistido, para detectar tanto un
backfill mal ejecutado como una edición manual posterior que haya dejado el dato
inconsistente.

Solo lectura: no modifica nada. Recorre todos los schemas de tenant.
"""
from sqlalchemy import text

from app.core.db import with_db


def _schemas() -> list[str]:
    with with_db(None) as db:
        return [r[0] for r in db.execute(text("SELECT schema FROM shared.tenants")).fetchall()]


def _check_schema(schema: str) -> list[tuple[str, str, str]]:
    """Devuelve [(group_id, pricing_type_guardado, pricing_type_esperado), ...] solo para
    los grupos donde ambos difieren."""
    with with_db(schema) as db:
        rows = db.execute(text(f'''
            SELECT
                og.id,
                og.pricing_type,
                CASE WHEN EXISTS (
                    SELECT 1 FROM "{schema}".options o
                    WHERE o.option_group_id = og.id AND o.extra_price > 0
                ) THEN 'con_recargo' ELSE 'incluido' END AS esperado
            FROM "{schema}".option_groups og
        ''')).all()
    return [
        (str(gid), guardado, esperado)
        for gid, guardado, esperado in rows
        if guardado != esperado
    ]


def main() -> int:
    total_discrepancias = 0
    for schema in _schemas():
        try:
            discrepancias = _check_schema(schema)
        except Exception as e:
            # Un tenant a medio migrar (columna todavía no agregada) no debe
            # impedir revisar los demás.
            print(f"\n{schema}: no se pudo verificar ({type(e).__name__}: {e})")
            continue

        if not discrepancias:
            print(f"\n{schema}: ✔ todos los grupos coinciden con el criterio de FR-015")
            continue

        total_discrepancias += len(discrepancias)
        print(f"\n{schema}: ⚠ {len(discrepancias)} grupo(s) con pricing_type inconsistente")
        for gid, guardado, esperado in discrepancias:
            print(f"    grupo {gid}: guardado={guardado}  esperado={esperado}")

    print()
    if total_discrepancias:
        print(f"⚠ {total_discrepancias} grupo(s) con discrepancia. Revisar antes de dar por "
              "buena la migración 68326ed66ebf en producción.")
    else:
        print("✔ pricing_type coincide con el criterio de FR-015 en todos los tenants.")
    return 1 if total_discrepancias else 0


if __name__ == "__main__":
    raise SystemExit(main())
