"""Subdominios reservados (spec 091, A-101): lista literal, `PLATFORM_HOST` y
rechazo en el alta de negocio.

    python -m unittest app.characterization_tests.test_reserved_hosts -v
"""
import json
import unittest
from uuid import uuid4

from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.v1.super_admin.schemas import TenantCreateWithUser
from app.core.error_response import envelope_from_validation_error
from app.core.reserved_hosts import (
    PLATFORM_HOST,
    RESERVED_SUBDOMAINS,
    is_reserved_subdomain,
)


class ReservedHostsListTests(unittest.TestCase):
    def test_la_lista_literal_y_el_host_de_plataforma_estan_fijados(self):
        self.assertEqual(
            RESERVED_SUBDOMAINS,
            frozenset({"www", "app", "admin", "assets", "api", "docs"}),
        )
        self.assertEqual(PLATFORM_HOST, "admin")

    def test_reservados_sin_distinguir_mayusculas_ni_espacios(self):
        for value in ("admin", "Admin", "ADMIN", " admin ", "Api", " DOCS ", "www", "app", "assets"):
            with self.subTest(value=value):
                self.assertTrue(is_reserved_subdomain(value))

    def test_igualdad_no_prefijo(self):
        for value in ("admin2", "admin-prueba", "mi-api", "docs1", "acme"):
            with self.subTest(value=value):
                self.assertFalse(is_reserved_subdomain(value))


def _payload(host: str) -> dict:
    return {
        "tenant_name": "Acme SAS",
        "schema_name": "acme_schema",
        "host": host,
        "name": "Dueño Acme",
        "email": "dueno@acme.com",
        "plan_id": uuid4(),
        "ciclo_facturacion": "mensual",
    }


class TenantCreateReservedHostTests(unittest.TestCase):
    def test_alta_con_host_reservado_se_rechaza_en_el_campo_host(self):
        for value, shown in (
            ("admin", "admin"),
            ("ADMIN", "admin"),
            (" api ", "api"),
            ("assets", "assets"),
            ("docs", "docs"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as ctx:
                    TenantCreateWithUser(**_payload(value))
                errors = ctx.exception.errors()
                self.assertEqual(errors[0]["loc"], ("host",))
                self.assertIn(
                    f"«{shown}» es una palabra reservada y no puede usarse como subdominio",
                    errors[0]["msg"],
                )

    def test_hosts_parecidos_pero_no_iguales_pasan_sin_transformar_el_valor(self):
        for value in ("admin2", "mi-api", "docs1", "acme"):
            with self.subTest(value=value):
                schema = TenantCreateWithUser(**_payload(value))
                self.assertEqual(schema.host, value)

    def test_el_rechazo_se_serializa_como_422_y_no_revienta_como_500(self):
        """Bajo `/super-admin` el handler arma el envelope con `exc.errors()`; el
        `ValueError` de `ctx.error` no es serializable y antes terminaba en 500."""
        with self.assertRaises(ValidationError) as ctx:
            TenantCreateWithUser(**_payload("admin"))

        status, body = envelope_from_validation_error(
            RequestValidationError(ctx.exception.errors()), "req-1"
        )

        self.assertEqual(status, 422)
        serialized = json.loads(json.dumps(body))
        self.assertIn(
            "«admin» es una palabra reservada y no puede usarse como subdominio",
            serialized["error"]["details"]["errors"][0]["msg"],
        )


if __name__ == "__main__":
    unittest.main()
