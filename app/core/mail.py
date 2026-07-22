import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

EMAIL_SEND_PATH = "/api/email/send"

def create_message(recipients: list[str], subject: str, body: str):
    return {
        "from": f"{settings.MAIL_FROM_NAME} <{settings.MAIL_FROM}>",
        "to": recipients,
        "subject": subject,
        "htmlBody": body,
    }

def send_email(message: dict):
    logger.info(message)
    url = f"{settings.EMAIL_API_URL.rstrip('/')}{EMAIL_SEND_PATH}"
    logger.info("Enviando email a %s con asunto '%s'", message["to"], message["subject"])
    try:
        response = httpx.post(url, json=message, timeout=10.0)
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        # La API respondió con un status de error: expone su cuerpo.
        detail = exc.response.text
        logger.error(
            "Email API respondió %s: %s", exc.response.status_code, detail
        )
        raise RuntimeError(
            f"Error del servicio de email ({exc.response.status_code}): {detail}"
        ) from exc
    except httpx.RequestError as exc:
        # No hubo respuesta (timeout, conexión, DNS…).
        logger.error("No se pudo contactar el servicio de email: %s", exc)
        raise RuntimeError(f"No se pudo contactar el servicio de email: {exc}") from exc


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