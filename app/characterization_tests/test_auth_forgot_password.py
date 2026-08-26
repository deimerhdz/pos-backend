"""POST /auth/forgot-password (spec 031, US1).

quickstart.md §US1 pasos 1-4 y 8. `enforce_sliding_window` y `send_email_task`
se mockean (research.md Decisión 10) — no hace falta Redis ni el
microservicio de correo reales.
"""
import asyncio
import re
import unittest
from unittest.mock import patch

from app.characterization_tests import auth_fixtures as af
from app.api.v1.auth import routes as auth_routes
from app.api.v1.auth.schemas import ForgotPasswordRequest

_GENERIC_MESSAGE = auth_routes._FORGOT_PASSWORD_GENERIC_MESSAGE["message"]


class ForgotPasswordTests(unittest.TestCase):
    def setUp(self):
        self.db = af.new_session()
        self.tenant = af.make_tenant(self.db)
        self.user = af.make_user(self.db, tenant=self.tenant, password="claveActual1")
        self.db.commit()

    def _call(self, email: str, tenant=None):
        with patch("app.api.v1.auth.routes.enforce_sliding_window", return_value=False), \
             patch("app.api.v1.auth.routes.send_email_task") as mock_send:
            resp = asyncio.run(
                auth_routes.forgot_password(
                    ForgotPasswordRequest(email=email),
                    tenant=tenant or self.tenant,
                    db=self.db,
                )
            )
        return resp, mock_send

    def test_existing_account_gets_generic_200_and_email_with_link(self):
        resp, mock_send = self._call(self.user.email)

        self.assertEqual(resp.status_code, 200)
        self.assertIn(_GENERIC_MESSAGE, resp.body.decode())
        mock_send.delay.assert_called_once()
        body_html = mock_send.delay.call_args.kwargs["body"]
        match = re.search(r"token=([^\"&]+)", body_html)
        self.assertIsNotNone(match, "el correo debe incluir el link con el token")

    def test_unknown_email_same_200_no_email_sent(self):
        resp, mock_send = self._call("nadie-existe@example.com")

        self.assertEqual(resp.status_code, 200)
        self.assertIn(_GENERIC_MESSAGE, resp.body.decode())
        mock_send.delay.assert_not_called()

    def test_inactive_account_same_treatment_as_unknown(self):
        self.user.active = False
        self.db.commit()

        resp, mock_send = self._call(self.user.email)

        self.assertEqual(resp.status_code, 200)
        self.assertIn(_GENERIC_MESSAGE, resp.body.decode())
        mock_send.delay.assert_not_called()

    def test_account_in_other_tenant_same_treatment_as_unknown(self):
        other_tenant = af.make_tenant(self.db)
        self.db.commit()

        resp, mock_send = self._call(self.user.email, tenant=other_tenant)

        self.assertEqual(resp.status_code, 200)
        self.assertIn(_GENERIC_MESSAGE, resp.body.decode())
        mock_send.delay.assert_not_called()

    def test_rate_limit_blocks_without_touching_db_or_email(self):
        """4ª solicitud dentro de la ventana → 429, sin fila nueva ni correo; la
        5ª (fuera de ventana) vuelve a 200 normal (FR-010, Acceptance Scenario 9).

        `enforce_sliding_window` se mockea con la secuencia que produciría una
        ventana deslizante genuina para 5 solicitudes pegadas al límite —
        la implementación real de la ventana no se re-ejercita aquí (vive en
        `rate_limit.py`, sin Redis en este suite); este test verifica que el
        router respeta su resultado (research.md Decisión 10)."""
        from fastapi import HTTPException

        blocked_sequence = [False, False, False, True, False]

        with patch(
            "app.api.v1.auth.routes.enforce_sliding_window",
            side_effect=blocked_sequence,
        ), patch("app.api.v1.auth.routes.send_email_task") as mock_send:
            for blocked in blocked_sequence:
                req = ForgotPasswordRequest(email=self.user.email)
                if blocked:
                    with self.assertRaises(HTTPException) as ctx:
                        asyncio.run(auth_routes.forgot_password(req, tenant=self.tenant, db=self.db))
                    self.assertEqual(ctx.exception.status_code, 429)
                else:
                    resp = asyncio.run(auth_routes.forgot_password(req, tenant=self.tenant, db=self.db))
                    self.assertEqual(resp.status_code, 200)

        # 4 correos despachados — todas las solicitudes menos la bloqueada.
        self.assertEqual(mock_send.delay.call_count, 4)


if __name__ == "__main__":
    unittest.main()
