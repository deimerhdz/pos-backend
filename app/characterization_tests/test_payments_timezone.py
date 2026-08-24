"""Spec 030 — Historia 2: `sales/schemas.py:PaymentResponse.paid_at` (campo
nuevo, research.md Decisión 9) usa el mismo mecanismo central (`UtcDatetime`)
que Ventas (A-50, registro-de-anomalias.md).

    python -m unittest app.characterization_tests.test_payments_timezone -v
"""
import unittest
import uuid
from datetime import datetime
from decimal import Decimal

from app.api.v1.sales import schemas as sales_schemas


class TestPaymentPaidAtUtcSerialization(unittest.TestCase):
    def test_paid_at_lleva_offset_utc_explicito(self):
        naive = datetime(2026, 8, 24, 12, 53, 7)
        resp = sales_schemas.PaymentResponse.model_construct(
            id=uuid.uuid4(), payment_method_id=uuid.uuid4(), amount=Decimal("10000"),
            paid_at=naive,
        )
        dumped = resp.model_dump(mode="json")["paid_at"]
        self.assertTrue(dumped.endswith("+00:00"), dumped)


if __name__ == "__main__":
    unittest.main()
