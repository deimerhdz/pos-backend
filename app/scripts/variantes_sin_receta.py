"""Informe de variantes que se venden sin descontar inventario.

    python -m app.scripts.variantes_sin_receta

Una variante sin filas en `recipe_items` (y sin opciones que liguen insumo) se
vende sin mover el stock: el kardex no registra nada y el inventario queda
sobrestimado en silencio hasta el conteo físico.

**Correr esto ANTES de desplegar el bloqueo por receta ausente.** A partir de ese
cambio, confirmar un pedido con una de estas variantes devuelve `409` y el producto
deja de poder venderse, así que la lista debe quedar vacía primero.

Solo lectura: no modifica nada. Recorre todos los schemas de tenant.
"""
from sqlalchemy import text

from app.core.db import with_db


def _schemas() -> list[str]:
    with with_db(None) as db:
        return [r[0] for r in db.execute(text("SELECT schema FROM shared.tenants")).fetchall()]


def _sin_receta(schema: str) -> list[dict]:
    """Variantes activas, de productos activos, que no consumen ningún insumo."""
    with with_db(schema) as db:
        return [dict(r) for r in db.execute(text(f'''
            SELECT p.name AS producto, pv.name AS variante,
                   p.preparation_type, pv.price, pv.id
            FROM "{schema}".product_variants pv
            JOIN "{schema}".products p ON p.id = pv.product_id
            WHERE pv.active AND p.active
              AND NOT EXISTS (
                  SELECT 1 FROM "{schema}".recipe_items ri
                  WHERE ri.product_variant_id = pv.id
              )
            ORDER BY p.name, pv.name
        ''')).mappings()]


def main() -> int:
    total = 0
    for schema in _schemas():
        try:
            filas = _sin_receta(schema)
        except Exception as e:
            # Un tenant a medio migrar no debe impedir revisar los demás.
            print(f"\n{schema}: no se pudo revisar ({type(e).__name__})")
            continue

        with with_db(schema) as db:
            activas = db.execute(text(f'''
                SELECT count(*) FROM "{schema}".product_variants pv
                JOIN "{schema}".products p ON p.id = pv.product_id
                WHERE pv.active AND p.active
            ''')).scalar()

        if not filas:
            print(f"\n{schema}: ✔ todas las variantes activas tienen receta ({activas})")
            continue

        total += len(filas)
        print(f"\n{schema}: ⚠ {len(filas)} de {activas} variantes activas SIN RECETA")
        for f in filas:
            print(f"    [{f['preparation_type']:9}] {f['producto']} · {f['variante']}"
                  f"  (${f['price']})")

    print()
    if total:
        print(f"⚠ {total} variante(s) se venden sin descontar inventario.")
        print("  Cárgales receta en Productos → Recetas antes de desplegar el bloqueo,")
        print("  o dejarán de poder venderse.")
    else:
        print("✔ Ninguna variante activa vende sin descontar. Listo para desplegar.")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
