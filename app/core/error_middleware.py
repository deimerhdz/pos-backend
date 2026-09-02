"""Traducción a HTTP de los errores del módulo super-admin, con alcance de
prefijo de ruta (spec 068).

Se apoya en dos piezas separadas, a propósito:

- `RequestIdMiddleware`: middleware ASGI que solo estampa un `request_id` por
  solicitud (para el prefijo dado) y, para ese mismo prefijo, atrapa
  cualquier excepción **no anticipada** que llegue hasta aquí sin haber sido
  ya convertida en una respuesta. Para cualquier otra ruta no hace nada
  distinto de hoy: ni genera `request_id` ni envuelve `call_next` en un
  `try/except` — así una falla no manejada en otro módulo sigue propagándose
  exactamente como hoy hasta `ServerErrorMiddleware` (Starlette), sin que
  este spec toque su comportamiento.
- `register_error_handlers`: registra los `@app.exception_handler` para
  `HTTPException`, `RequestValidationError` y `DomainError`. Estos tres tipos
  los intercepta `ExceptionMiddleware` de Starlette **antes** de que la
  excepción llegue a `RequestIdMiddleware` — por eso no se atrapan ahí, sino
  aquí, inspeccionando `request.url.path` para decidir si envolver la
  respuesta o delegar en el handler por defecto de FastAPI/Starlette.

Deliberadamente **no** se registra un `@app.exception_handler(Exception)`
global: Starlette enruta ese caso a `ServerErrorMiddleware` (el middleware
más externo, fuera de cualquier middleware de usuario), donde reemplazar su
comportamiento por defecto para otras rutas exigiría reproducir manualmente
una pieza interna de Starlette — con el riesgo real de dejar una ruta de
otro módulo sin respuesta ante un 500 no manejado. Atrapar la excepción no
anticipada en `RequestIdMiddleware`, solo para el prefijo de super-admin,
consigue lo mismo sin ese riesgo.

Cómo migrar otro módulo a este mismo patrón, más adelante: hoy
`RequestIdMiddleware`/`register_error_handlers` aceptan un único
`path_prefix: str` (`app/main.py` solo pasa `SUPER_ADMIN_ERROR_PREFIX`) —
llamarlos una segunda vez con otro prefijo **no** basta, porque
`@app.exception_handler` registra por tipo de excepción: la segunda llamada
sobrescribiría los handlers de la primera, dejando sin envelope al primer
prefijo. Cuando de verdad haga falta un segundo módulo, el cambio es
mecánico y pequeño — que `path_prefix` acepte una tupla de prefijos en
ambas funciones — pero no vale la pena construirlo antes de tener un
segundo módulo real que lo necesite. En cualquier caso, no hace falta
duplicar `domain_errors.py`, `error_response.py` ni este archivo: ya son
infraestructura reutilizable, independiente de super-admin.
"""

from __future__ import annotations

import logging
import uuid
from typing import Awaitable, Callable

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler as default_http_exception_handler,
)
from fastapi.exception_handlers import (
    request_validation_exception_handler as default_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.domain_errors import DomainError
from app.core.error_response import (
    envelope_from_domain_error,
    envelope_from_http_exception,
    envelope_from_unexpected_exception,
    envelope_from_validation_error,
)

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


def _report_unexpected_exception(request: Request, exc: Exception, request_id: str, *, module: str) -> None:
    operation = f"{request.method} {request.url.path}"
    user_id = getattr(request.state, "super_admin_id", None)

    # Mismo contexto que se envía a Sentry (module/operation/request_id/
    # user_id), en la terminal, siempre — sin importar el entorno, para que
    # se pueda depurar en desarrollo igual que se correlaciona en producción
    # (la traza completa se agrega sola: `logger.exception` adjunta
    # `exc_info` después de este mensaje).
    logger.exception(
        "Falla técnica inesperada | module=%s operation=%s request_id=%s user_id=%s",
        module, operation, request_id, user_id if user_id is not None else "-",
    )

    # Fuera de producción, sentry_sdk.init() nunca se llamó (app/main.py) — sin
    # cliente activo, capture_exception ya sería un no-op, pero se corta antes
    # para dejar explícito el gate (research.md § 6) y no depender de eso.
    if settings.ENVIRONMENT != "prod":
        return

    with sentry_sdk.push_scope() as scope:
        scope.set_tag("module", module)
        scope.set_tag("operation", operation)
        scope.set_tag("request_id", request_id)
        if user_id is not None:
            scope.set_user({"id": str(user_id)})
        sentry_sdk.capture_exception(exc)


class RequestIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, path_prefix: str, *, module: str = "super-admin"):
        super().__init__(app)
        self.path_prefix = path_prefix
        self.module = module

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not request.url.path.startswith(self.path_prefix):
            return await call_next(request)

        request.state.request_id = str(uuid.uuid4())
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 - punto de captura deliberado
            request_id = _request_id(request)
            _report_unexpected_exception(request, exc, request_id, module=self.module)
            status_code, body = envelope_from_unexpected_exception(request_id)
            return JSONResponse(body, status_code=status_code)


def register_error_handlers(app: FastAPI, path_prefix: str, *, module: str = "super-admin") -> None:
    """Registra los handlers de traducción a HTTP con alcance a `path_prefix`.

    Para cualquier ruta que no empiece por `path_prefix`, cada handler delega
    en el comportamiento por defecto de FastAPI/Starlette — ninguna otra ruta
    del backend cambia de forma de respuesta por este registro.
    """

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        if not request.url.path.startswith(path_prefix):
            return await default_http_exception_handler(request, exc)
        request_id = _request_id(request)
        if exc.status_code >= 500:
            # Un HTTPException >=500 sigue siendo una falla técnica, así ya
            # venga envuelta desde más abajo (p. ej. `tenant_create` en
            # `app/core/db.py`, que atrapa cualquier excepción inesperada y
            # la relanza como HTTPException(500, "Internal server error")).
            # Sin esto, ese camino nunca pasaba por `RequestIdMiddleware` (ya
            # llega convertido en respuesta antes de esa capa) y ni el
            # `request_id` ni el reporte a Sentry quedaban correlacionados
            # con el error real — capturar `exc` aquí igual reporta a Sentry
            # la excepción original completa, por el encadenamiento
            # implícito de Python (`__cause__`/`__context__`).
            _report_unexpected_exception(request, exc, request_id, module=module)
        status_code, body = envelope_from_http_exception(exc, request_id)
        return JSONResponse(body, status_code=status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):
        if not request.url.path.startswith(path_prefix):
            return await default_validation_exception_handler(request, exc)
        status_code, body = envelope_from_validation_error(exc, _request_id(request))
        return JSONResponse(body, status_code=status_code)

    @app.exception_handler(DomainError)
    async def _domain_error_handler(request: Request, exc: DomainError):
        # DomainError es un tipo nuevo que hoy nadie más lanza (data-model.md
        # § 1): no hace falta comprobar el prefijo, pero se deja expresado
        # igual por claridad y por si algún día deja de ser exclusivo.
        status_code, body = envelope_from_domain_error(exc, _request_id(request))
        return JSONResponse(body, status_code=status_code)
