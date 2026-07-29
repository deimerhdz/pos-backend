from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, ForeignKey, DateTime, func, CheckConstraint, Index, text
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from .session_participant import SessionParticipant


class TableSession(UUIDPrimaryKeyMixin, Base):
    """Sesión **de mesa**: el ciclo de vida compartido por todos los comensales que
    se sientan juntos, desde que el primero escanea el QR hasta que el staff cobra
    y libera la mesa.

    Es el agrupador que faltaba: antes la "sesión" era por comensal
    (`dining_sessions`, hoy `session_participants`) y la agrupación por mesa era
    implícita. Sin esta entidad no había dónde poner un `billing_mode`, un
    `closed_by` ni un timeout de mesa.

    Invariantes:
    - **una sola sesión `active` por mesa** (índice parcial `idx_active_session_per_table`);
      escanear el QR de una mesa con sesión activa une al comensal a esa sesión,
      no crea otra;
    - solo el staff la cierra (el comensal anónimo no puede);
    - `billing_mode` es null hasta el cierre: lo elige el cajero al cobrar.
    """

    __tablename__ = "table_sessions"

    dining_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="active")

    opened_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Referencia blanda a shared.users.id + snapshot del nombre: quién cerró la
    # mesa. Sin FK cross-schema (pelearía con schema_translate_map). Null cuando
    # la cerró el job de sesiones huérfanas y no un humano.
    closed_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    closed_by_user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Cómo se dividió la cuenta al cobrar. Null mientras la sesión está activa.
    billing_mode: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    participants: Mapped[List["SessionParticipant"]] = relationship(
        back_populates="table_session"
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'closed')", name="ck_table_session_status"),
        CheckConstraint(
            "billing_mode IS NULL OR billing_mode IN ('unified', 'split')",
            name="ck_table_session_billing_mode",
        ),
        Index(
            "idx_active_session_per_table",
            "dining_table_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        {"schema": "tenant"},
    )
