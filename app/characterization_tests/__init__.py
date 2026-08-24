"""Tests de caracterización de `pos-backend`.

Congelan el comportamiento ACTUAL del sistema (Constitución de `pos-specs`,
principio I: "el comportamiento actual es sagrado"). No son tests de
aceptación: si uno de estos tests falla tras un cambio de código, la
pregunta correcta no es "¿cómo lo arreglo?" sino "¿el negocio autorizó
este cambio de comportamiento?".

Alcance de esta primera red (ver `specs/000-reconocimiento/` en el
repositorio `pos-specs` para la evidencia completa de cada regla/anomalía
citada en los nombres de test):

  - `app/api/v1/catalog/line_pricing.py` y `consumption_plan.py`: el motor
    de precio de línea y de "qué se descuenta de inventario" (módulo de
    criticidad ALTA `catalog`).
  - `app/api/v1/inventory/stock.py`: el único punto de mutación de stock
    (módulo ALTA `inventory`).
  - `app/core/units.py`, `app/core/inventory_reasons.py`: utilidades de las
    que dependen los módulos ALTA.
  - `app/api/v1/invoices/schemas.py` (`Invoice.full_number`): formato del
    número de factura visible.
  - Golden master del núcleo de cálculo del flujo de pedido de mesa por QR
    (precio de línea + validación de opciones + plan de consumo), el flujo
    identificado como central en `flujo-pedido-qr.md`.

Requiere `unittest` (biblioteca estándar) exclusivamente — cero
dependencias nuevas (Constitución, principio IV). Se ejecuta contra los
modelos SQLAlchemy reales del proyecto sobre SQLite en memoria (mismo
mecanismo `schema_translate_map` que usa `app/core/db.py:with_db` en
producción para resolver el schema por tenant), no contra mocks: lo que
se observa es exactamente lo que produce el código real.

Ejecutar toda la red:

    python -m unittest discover -s app/characterization_tests -p 'test_*.py' -v

(o cada módulo suelto, p. ej. `python -m unittest
app.characterization_tests.test_catalog_line_pricing -v`).
"""
