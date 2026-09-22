"""Generación del `.xlsx` de respaldo completo de inventario (spec 086,
GET /inventory/items/export). El workbook se arma completo en memoria antes
de responder — ver research.md §2 para el porqué (garantiza que un error a
mitad de la generación nunca deja un archivo corrupto/incompleto en la
respuesta)."""
import io
from decimal import Decimal
from uuid import UUID

from openpyxl import Workbook

from app.models.inventory_item import InventoryItem
from app.models.unit_measure import UnitMeasure

HEADERS = ["Nombre", "Tipo", "Unidad", "Stock", "Mínimo", "Costo", "Estado"]


def _type_label(type: str) -> str:
    """Replica `typeLabel()` (inventory-page.component.ts:488-490)."""
    return "Empacado" if type == "packaged" else "Materia prima"


def _status_label(active: bool, current_stock: Decimal, min_stock: Decimal) -> str:
    """Replica `isLow()` (inventory-page.component.ts:491-493), incluida la
    condición `active` — un insumo inactivo siempre exporta "OK" (research.md §4)."""
    return "Bajo mínimo" if active and current_stock <= min_stock else "OK"


def build_inventory_excel(
    items: list[InventoryItem],
    unit_measures_by_id: dict[UUID, UnitMeasure],
) -> io.BytesIO:
    """Arma el workbook completo en memoria: fila 1 de encabezados (FR-004) y una
    fila por cada `InventoryItem` recibido, con Stock/Mínimo/Costo como valores
    numéricos nativos (FR-006, sin redondear ni truncar)."""
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)

    for item in items:
        unit = unit_measures_by_id.get(item.unit_measure_id)
        ws.append([
            item.name,
            _type_label(item.type),
            unit.abbreviation if unit is not None else "",
            item.current_stock,
            item.min_stock,
            item.unit_cost,
            _status_label(item.active, item.current_stock, item.min_stock),
        ])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
