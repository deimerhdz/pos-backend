"""Mecanismo central de zona horaria (spec 030, FR-002).

Todas las entidades con un instante absoluto (Venta, Orden, Pago, Caja,
Inventario, Mesas, Facturas, Compras, Auditoría) pasan por aquí: una sola
resolución de zona horaria por tenant, un solo tipo de serialización con
offset UTC explícito, un solo cálculo de límites de día de negocio, y un solo
envoltorio de "ahora". Ningún módulo de entidad implementa su propia
conversión (ver spec.md FR-002).
"""
from datetime import date, datetime, time, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import PlainSerializer

from app.core.config import settings
from app.core.models import Tenant


def resolve_timezone(tenant: "Tenant | None") -> ZoneInfo:
    """Zona horaria del tenant, con `America/Bogota`/`TENANT_TIMEZONE` como
    respaldo si el tenant no llega o su columna aún es `None` (no debería
    serlo tras la migración, pero el respaldo evita un None-crash)."""
    if tenant is not None and tenant.timezone:
        return ZoneInfo(tenant.timezone)
    return ZoneInfo(settings.TENANT_TIMEZONE)


def _serialize_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


UtcDatetime = Annotated[datetime, PlainSerializer(_serialize_utc, return_type=str)]


def local_day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Medianoche local de `day` y del día siguiente, ambas en UTC naive —
    mismo tipo que las columnas `DateTime` que compara (FR-004)."""
    next_day = date.fromordinal(day.toordinal() + 1)
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = datetime.combine(next_day, time.min, tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def utc_now() -> datetime:
    """Envoltorio único de `datetime.now(timezone.utc)` (FR-008) — reemplaza
    los sitios que hoy construyen su propio "ahora" para asignarlo a una
    columna `DateTime` naive persistida."""
    return datetime.now(timezone.utc)
