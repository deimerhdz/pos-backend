"""Construcción del envelope de error consistente del módulo super-admin (spec 068).

Traduce tres orígenes posibles de error a la misma forma de respuesta
(`error-envelope.md`):

- una excepción de dominio (`app/core/domain_errors.DomainError`);
- un `HTTPException`/`RequestValidationError` ya producido por el resto del
  código (p. ej. `get_or_404`, `ensure_unique`, `validate_billing_cycle_price`,
  o la validación de Pydantic de FastAPI) — no se toca su origen, solo se
  reempaqueta su respuesta;
- una excepción no anticipada, de la que nunca se expone su mensaje real.

Este módulo sí depende de FastAPI/Starlette (es la capa HTTP), a diferencia de
`domain_errors.py`.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.domain_errors import DomainError

#: Mensaje genérico y seguro para toda falla técnica inesperada. Nunca se
#: sustituye por el texto real de la excepción (spec.md FR-003).
GENERIC_INTERNAL_ERROR_MESSAGE = "Ocurrió un error inesperado. Intenta de nuevo."

#: Mapeo código HTTP -> `error.code` por defecto, para respuestas que no traen
#: ya un código específico de dominio (research.md § 8).
STATUS_CODE_TO_GENERIC_CODE: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "INVALID_INPUT",
}


def build_envelope(
    *,
    code: str,
    message: str,
    request_id: str,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Arma el cuerpo de respuesta consistente.

    Conserva `detail` de nivel superior, igual a `message`, por compatibilidad
    con los consumidores existentes que hoy leen ese campo (spec.md §
    Clarifications).
    """
    return {
        "success": False,
        "error": {"code": code, "message": message, "details": details},
        "request_id": request_id,
        "detail": message,
    }


def envelope_from_domain_error(exc: DomainError, request_id: str) -> tuple[int, dict[str, Any]]:
    body = build_envelope(code=exc.code, message=exc.message, details=exc.details, request_id=request_id)
    return exc.default_status_code, body


def envelope_from_http_exception(exc: StarletteHTTPException, request_id: str) -> tuple[int, dict[str, Any]]:
    code = STATUS_CODE_TO_GENERIC_CODE.get(exc.status_code, f"HTTP_{exc.status_code}")
    detail = exc.detail
    if isinstance(detail, str):
        message, details = detail, None
    elif detail is None:
        message, details = "Error", None
    else:
        # Algún llamador pasó un `detail` estructurado (dict/list) en vez de texto:
        # se preserva como `details` y se usa un mensaje genérico legible por
        # humanos, en vez de volcar la estructura cruda como mensaje.
        message, details = "Error de la solicitud", {"raw": detail} if not isinstance(detail, dict) else detail
    return exc.status_code, build_envelope(code=code, message=message, details=details, request_id=request_id)


def envelope_from_validation_error(exc: RequestValidationError, request_id: str) -> tuple[int, dict[str, Any]]:
    return 422, build_envelope(
        code="INVALID_INPUT",
        message="Los datos enviados no son válidos.",
        details={"errors": exc.errors()},
        request_id=request_id,
    )


def envelope_from_unexpected_exception(request_id: str) -> tuple[int, dict[str, Any]]:
    return 500, build_envelope(
        code="INTERNAL_ERROR",
        message=GENERIC_INTERNAL_ERROR_MESSAGE,
        details=None,
        request_id=request_id,
    )
