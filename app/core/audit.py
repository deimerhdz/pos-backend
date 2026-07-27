"""Helper de auditoría (RF-076). `record_audit` inserta una fila en `audit_logs`
del schema del tenant. No hace commit: se une a la transacción del caller."""
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def record_audit(
    db: Session,
    *,
    action: str,
    entity: str,
    entity_id: UUID | None = None,
    user=None,
    payload: dict | None = None,
) -> None:
    db.add(AuditLog(
        action=action,
        entity=entity,
        entity_id=entity_id,
        user_id=getattr(user, "id", None),
        user_name=getattr(user, "name", None),
        payload=payload,
    ))
