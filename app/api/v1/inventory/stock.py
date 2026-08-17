"""Fachada: reexporta el motor de stock desde `app.inventory_engine`
(specs/018-extraccion-motor-inventario). Sin lógica propia de cálculo,
validación o consulta — ver contracts/module-api.md Contrato B.
"""
from app.inventory_engine import (
    lock_items as lock_items,
    record_movement as record_movement,
    apply_adjustment as apply_adjustment,
)
