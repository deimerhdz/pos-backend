from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, ForeignKey, DateTime, func, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from .table_session import TableSession


class SessionParticipant(UUIDPrimaryKeyMixin, Base):
    """Un comensal anónimo dentro de una `table_session` (antes `dining_sessions`).

    `display_name` es lo que el cliente escribe al escanear el QR: **no es único
    ni es el identificador**. El identificador real es el `id` de esta fila,
    firmado dentro del token de sesión (claim `s`); un nombre repetido no permite
    suplantar a nadie.

    `display_label` es el nombre ya desambiguado que ven cocina y staff: si dos
    comensales de la misma sesión escriben "Ana", el segundo queda como "Ana (2)".
    Se calcula al insertar, no al leer.

    `status` es del comensal, no de la mesa: puede irse (`closed`) mientras la
    `table_session` sigue activa. `expires_at` es el TTL deslizante de su token.
    """

    __tablename__ = "session_participants"

    table_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("table_sessions.id"), nullable=False, index=True
    )
    table_session: Mapped["TableSession"] = relationship(back_populates="participants")

    # Denormalizado desde table_session (ya venía indexado y lo usan las consultas
    # de cuenta/split); table_session_id es la fuente de verdad.
    dining_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=False, index=True
    )

    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    display_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="open")

    joined_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # TTL deslizante del token del comensal (se corre en cada actividad).
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'closed')", name="ck_session_participant_status"
        ),
        {"schema": "tenant"},
    )
