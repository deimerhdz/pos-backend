"""Spec 030 — Historia 2: Inventario (`MovementResponse`, `PurchaseResponse`)
usa el mismo mecanismo central (`UtcDatetime`) que Ventas (A-50,
registro-de-anomalias.md).

    python -m unittest app.characterization_tests.test_inventory_timezone -v
"""
import unittest
import uuid
from datetime import datetime
from decimal import Decimal

from app.api.v1.inventory import schemas as inventory_schemas
from app.core.timezone import resolve_timezone


class TestMovementUtcSerialization(unittest.TestCase):
    def test_moved_at_lleva_offset_utc_explicito(self):
        naive = datetime(2026, 8, 24, 12, 53, 7)
        resp = inventory_schemas.MovementResponse.model_construct(
            id=uuid.uuid4(), inventory_item_id=uuid.uuid4(), type="out",
            quantity=Decimal("1.5"), moved_at=naive,
        )
        dumped = resp.model_dump(mode="json")["moved_at"]
        self.assertTrue(dumped.endswith("+00:00"), dumped)

    def test_movimiento_23_59_bogota_se_ve_como_el_dia_de_bogota_correcto(self):
        # 23:59 hora de Bogota del 24/08 = 04:59 UTC del 25/08.
        naive_utc = datetime(2026, 8, 25, 4, 59, 0)
        resp = inventory_schemas.MovementResponse.model_construct(
            id=uuid.uuid4(), inventory_item_id=uuid.uuid4(), type="out",
            quantity=Decimal("1.5"), moved_at=naive_utc,
        )
        dumped = resp.model_dump(mode="json")["moved_at"]
        local = datetime.fromisoformat(dumped).astimezone(resolve_timezone(None))
        self.assertEqual(local.date().isoformat(), "2026-08-24")
        self.assertEqual((local.hour, local.minute), (23, 59))


class TestPurchaseUtcSerialization(unittest.TestCase):
    def test_purchased_at_lleva_offset_utc_explicito(self):
        naive = datetime(2026, 8, 24, 12, 53, 7)
        resp = inventory_schemas.PurchaseResponse.model_construct(
            id=uuid.uuid4(), status="received", total=Decimal("50000"), purchased_at=naive,
        )
        dumped = resp.model_dump(mode="json")["purchased_at"]
        self.assertTrue(dumped.endswith("+00:00"), dumped)


if __name__ == "__main__":
    unittest.main()
