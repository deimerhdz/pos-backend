"""CONGELA app/api/v1/invoices/schemas.py:InvoiceResponse.full_number — el
formato Python del número de factura visible.

Referencia: `reglas-de-negocio.md` RN-FACT-05/RN-FACT-07 y
`registro-de-anomalias.md` A-14 (bug matemáticamente cierto: Python NO
trunca un número que exceda 6 dígitos; la reconstrucción SQL con `lpad`
sí trunca — aquí solo se congela el lado Python, ejecutable sin BD; el lado
SQL requiere Postgres real y queda fuera de esta red, ver README de esta
carpeta / resumen de cobertura).
"""
import unittest
import uuid
from datetime import datetime
from decimal import Decimal

from app.api.v1.invoices.schemas import InvoiceResponse


def _invoice(prefix: str, number: int) -> InvoiceResponse:
    return InvoiceResponse(
        id=uuid.uuid4(),
        sale_id=uuid.uuid4(),
        prefix=prefix,
        number=number,
        subtotal=Decimal("0"),
        discount=Decimal("0"),
        tax=Decimal("0"),
        tip=Decimal("0"),
        total=Decimal("0"),
        status="paid",
        issued_at=datetime(2026, 1, 1),
    )


class FullNumberFormatTests(unittest.TestCase):
    def test_rn_fact_relleno_a_6_digitos_con_ceros_a_la_izquierda(self):
        self.assertEqual(_invoice("FAC-", 42).full_number, "FAC-000042")

    def test_boundary_999999_cabe_exacto_en_6_digitos(self):
        self.assertEqual(_invoice("FAC-", 999999).full_number, "FAC-999999")

    def test_a14_boundary_1000000_no_trunca_produce_7_digitos(self):
        """A-14: al cruzar el millón, `:06d` es un ANCHO MÍNIMO, no un tope —
        Python nunca trunca. El número visible pasa a tener 7 dígitos."""
        self.assertEqual(_invoice("FAC-", 1000000).full_number, "FAC-1000000")

    def test_a14_boundary_1000001_tampoco_trunca(self):
        self.assertEqual(_invoice("FAC-", 1000001).full_number, "FAC-1000001")

    def test_a14_caso_documentado_1234567(self):
        """Caso exacto citado en contradiccion-04...md: Python produce
        'FAC-1234567' (11 caracteres); el equivalente SQL (`lpad(..., 6, '0')`,
        no verificado aquí por requerir Postgres real) trunca a 'FAC-123456',
        que es una referencia distinta y ya asignada a la factura #123456."""
        full = _invoice("FAC-", 1234567).full_number
        self.assertEqual(full, "FAC-1234567")
        self.assertNotEqual(full, "FAC-123456")

    def test_prefijo_vacio_solo_deja_los_digitos(self):
        self.assertEqual(_invoice("", 7).full_number, "000007")


if __name__ == "__main__":
    unittest.main()
