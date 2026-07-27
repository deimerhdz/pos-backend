from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional, Any
from datetime import datetime


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Bitácora de auditoría de operaciones críticas (RF-076). `user_id` es una
    referencia blanda a shared.users.id; `payload` guarda un snapshot opcional."""

    __tablename__ = "audit_logs"

    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[Optional[UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    user_id: Mapped[Optional[UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    payload: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)

    at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = ({"schema": "tenant"},)
