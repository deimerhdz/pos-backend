"""Excepciones de dominio/aplicación reutilizables (spec 068).

Deliberadamente sin ningún import de FastAPI/Starlette: estas excepciones deben
poder lanzarse y verificarse sin construir ni simular una solicitud HTTP
(spec.md FR-006). La traducción a una respuesta HTTP concreta vive en
`app/core/error_response.py` y en `app/core/error_middleware.py`.

Nacen para el módulo `super-admin` (`app/api/v1/super_admin/`), pero no tienen
ninguna dependencia de ese módulo: cualquier otro módulo puede reutilizarlas
más adelante activando el mismo middleware para su propio prefijo de ruta
(ver `app/core/error_middleware.py`).
"""

from typing import Any, Optional


class DomainError(Exception):
    """Base de toda excepción de negocio clasificada de este patrón.

    `code` es el identificador estable que `error_response.py` copia tal cual
    en `error.code` de la respuesta; `message` es el texto seguro que se
    muestra a quien llama (nunca debe incluir detalles técnicos internos).
    """

    #: Código de estado HTTP por defecto para esta categoría. Cada subclase lo
    #: fija; `error_response.py` lo usa salvo que el punto de origen indique
    #: otro explícitamente.
    default_status_code: int = 400

    def __init__(self, message: str, *, code: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__.upper()
        self.details = details


class EntityNotFoundError(DomainError):
    """El recurso referenciado (tenant, plan, entrada de catálogo, ...) no existe."""

    default_status_code = 404

    def __init__(self, entity: str, identifier: Any, *, message: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        self.entity = entity
        self.identifier = identifier
        code = f"{entity.strip().upper().replace(' ', '_')}_NOT_FOUND"
        super().__init__(message or f"No existe {entity} con el identificador dado", code=code, details=details)


class ConflictError(DomainError):
    """La operación colisiona con el estado actual de los datos (duplicados, etc.)."""

    default_status_code = 409

    def __init__(self, message: str, *, code: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        super().__init__(message, code=code or "CONFLICT", details=details)


class InvalidStateError(DomainError):
    """La operación no es válida para el estado actual del recurso."""

    default_status_code = 409

    def __init__(self, message: str, *, code: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        super().__init__(message, code=code or "INVALID_STATE", details=details)


class BusinessRuleViolation(DomainError):
    """Se incumple una regla de negocio que no encaja en las categorías anteriores."""

    default_status_code = 400

    def __init__(self, message: str, *, code: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        super().__init__(message, code=code or "BUSINESS_RULE_VIOLATION", details=details)


class UnauthorizedError(DomainError):
    """Ausencia de una identidad autenticada válida (distinto de "sin permiso")."""

    default_status_code = 401

    def __init__(self, message: str = "No autenticado", *, code: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        super().__init__(message, code=code or "UNAUTHORIZED", details=details)


class ForbiddenError(DomainError):
    """Identidad válida, pero sin el rol/permiso requerido para la operación."""

    default_status_code = 403

    def __init__(self, message: str = "No tienes permiso para esta operación", *, code: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        super().__init__(message, code=code or "FORBIDDEN", details=details)
