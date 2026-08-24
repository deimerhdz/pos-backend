"""Script interno: fija la zona horaria IANA de un tenant (spec 030, reapertura
de A-46). Sin pantalla de autoservicio — camino único de escritura junto con
el `server_default='America/Bogota'` de la migración (Clarifications).

Uso:
    python -m app.scripts.set_tenant_timezone --host central.skeilopos.com --timezone America/Mexico_City

Un valor que no sea un nombre IANA reconocido se rechaza antes de escribirse
— `Tenant.timezone` valida con `zoneinfo.ZoneInfo` en el propio modelo
(`@validates`, `app/core/models.py`), así que la excepción se lanza al asignar
el atributo, antes de cualquier `commit()`.
"""
import argparse
import logging

from sqlalchemy import select

from app.core.db import with_db
from app.core.models import Tenant

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def set_tenant_timezone(host: str, timezone: str) -> Tenant:
    with with_db(None) as db:
        tenant = db.execute(
            select(Tenant).where(Tenant.host == host)
        ).scalar_one_or_none()
        if tenant is None:
            raise RuntimeError(f"No existe ningún tenant con host {host!r}.")

        try:
            tenant.timezone = timezone
        except ValueError as exc:
            raise RuntimeError(
                f"Zona horaria inválida {timezone!r}: {exc}. No se persistió ningún cambio."
            ) from exc

        db.commit()
        db.refresh(tenant)
        logger.info("Tenant %r (%s) ahora usa la zona horaria %r.", host, tenant.name, timezone)
        return tenant


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fija la zona horaria IANA de un tenant existente."
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--timezone", required=True)
    args = parser.parse_args()

    set_tenant_timezone(host=args.host, timezone=args.timezone)


if __name__ == "__main__":
    main()
