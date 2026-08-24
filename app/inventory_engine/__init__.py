"""Motor de stock de inventario. Extracción de
`app/api/v1/inventory/stock.py` (specs/018-extraccion-motor-inventario).
Reexporta los tres símbolos públicos del contrato — ver
contracts/module-api.md Contrato A.
"""
from app.inventory_engine.stock import (
    lock_items as lock_items,
    record_movement as record_movement,
    apply_adjustment as apply_adjustment,
)
