"""Saludo del correo de invitación (spec 091, A-100).

    python -m unittest app.characterization_tests.test_invitation_email_greeting -v
"""
import unittest

from app.core.mail import invitation_email_body

_KW = dict(
    tenant_name="Acme",
    login_url="https://acme.skeilopos.com/login",
    email="ana@acme.com",
    password="Temporal123!",
)


class InvitationEmailGreetingTests(unittest.TestCase):
    def test_con_nombre_saluda_por_el_nombre(self):
        body = invitation_email_body(**_KW, name="María Pérez")
        self.assertIn("Hola, María Pérez:", body)

    def test_el_apostrofe_se_escapa(self):
        body = invitation_email_body(**_KW, name="O'Brien")
        self.assertIn("Hola, O&#x27;Brien:", body)
        self.assertNotIn("O'Brien", body)

    def test_sin_nombre_saluda_hola_y_nunca_con_el_correo(self):
        for name in (None, ""):
            with self.subTest(name=name):
                body = invitation_email_body(**_KW, name=name)
                self.assertIn("Hola:", body)
                self.assertNotIn("Hola, ", body)

    def test_el_resto_del_cuerpo_no_cambia(self):
        for body in (invitation_email_body(**_KW), invitation_email_body(**_KW, name="Ana Pérez")):
            self.assertIn("Te invitaron a unirte a Acme", body)
            self.assertIn("https://acme.skeilopos.com/login", body)
            self.assertIn("ana@acme.com", body)
            self.assertIn("Temporal123!", body)
            self.assertIn("Al iniciar sesión por primera vez deberás fijar tu propia contraseña", body)

    def test_los_llamadores_sin_name_siguen_funcionando(self):
        self.assertIn("Hola:", invitation_email_body("Acme", "https://x/login", "a@b.co", "pw"))


if __name__ == "__main__":
    unittest.main()
