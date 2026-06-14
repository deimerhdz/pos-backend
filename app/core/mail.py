import resend
from .config import settings

resend.api_key = settings.RESEND_API_KEY

def create_message(recipients: list[str], subject: str, body: str):
    return {
        "from": f"{settings.MAIL_FROM_NAME} <{settings.MAIL_FROM}>",
        "to": recipients,
        "subject": subject,
        "html": body,
    }
    
def send_email(message: dict):
    response = resend.Emails.send(message)
    return response


def welcome_email_body(tenant_name: str, login_url: str, email: str, password: str) -> str:
    return f"""\
<div style="font-family: Arial, sans-serif; max-width: 560px; margin: 0 auto; color: #1f2937;">
  <h2 style="color: #111827;">¡Bienvenido a {tenant_name}!</h2>
  <p>Tu cuenta ha sido creada correctamente. Estos son tus datos de acceso:</p>
  <table style="border-collapse: collapse; margin: 16px 0;">
    <tr>
      <td style="padding: 8px 12px; font-weight: bold;">URL de inicio de sesión</td>
      <td style="padding: 8px 12px;"><a href="{login_url}">{login_url}</a></td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; font-weight: bold;">Usuario</td>
      <td style="padding: 8px 12px;">{email}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; font-weight: bold;">Contraseña</td>
      <td style="padding: 8px 12px;">{password}</td>
    </tr>
  </table>
  <p style="color: #b91c1c; font-size: 14px;">
    Por seguridad, te recomendamos cambiar tu contraseña después de iniciar sesión por primera vez.
  </p>
</div>"""