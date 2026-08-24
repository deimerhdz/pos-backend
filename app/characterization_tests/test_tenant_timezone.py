"""Spec 030 — Historia 4: zona horaria configurable por tenant (reapertura de
A-46). Dos tenants con zonas distintas muestran sus propias fechas sin
afectarse entre sí, y un valor no-IANA se rechaza antes de persistirse
(Clarifications, FR-005).

No toca Postgres: ejercita `resolve_timezone` y el validador del modelo
directamente, con instancias `Tenant` en memoria (sin sesión), mismo patrón
que `app/scripts/test_promotions_rules.py` para funciones puras.

    python -m unittest app.characterization_tests.test_tenant_timezone -v
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.models import Tenant
from app.core.timezone import resolve_timezone


def _tenant(timezone: str) -> Tenant:
    return Tenant(
        id=1, name="t", schema="t", plan="basic", host="t.example.com", timezone=timezone,
    )


class TestDosTenantsZonasHorariasDistintas(unittest.TestCase):
    def test_cada_tenant_convierte_con_su_propia_zona_sin_afectar_al_otro(self):
        bogota = _tenant("America/Bogota")
        mexico = _tenant("America/Mexico_City")

        naive_utc = datetime(2026, 8, 24, 12, 0, 0)
        hora_bogota = naive_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(resolve_timezone(bogota))
        hora_mexico = naive_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(resolve_timezone(mexico))

        self.assertEqual(hora_bogota.hour, 7)
        self.assertEqual(hora_mexico.hour, 6)
        # Resolver uno no afecta al otro (Historia 4, Escenario 2).
        self.assertEqual(resolve_timezone(bogota).key, "America/Bogota")
        self.assertEqual(resolve_timezone(mexico).key, "America/Mexico_City")

    def test_tenant_sin_zona_configurada_usa_america_bogota_por_defecto(self):
        # Historia 4, Escenario 1: server_default cubre a todo tenant existente.
        self.assertEqual(resolve_timezone(None).key, "America/Bogota")


class TestRechazoDeZonaHorariaInvalida(unittest.TestCase):
    def test_zona_no_iana_nunca_se_asigna(self):
        tenant = _tenant("America/Bogota")
        with self.assertRaises(ValueError):
            tenant.timezone = "No/Existe"
        # El validador rechaza en el momento de asignar: el valor previo queda intacto.
        self.assertEqual(tenant.timezone, "America/Bogota")


if __name__ == "__main__":
    unittest.main()
