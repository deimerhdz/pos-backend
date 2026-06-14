
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Boolean, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .tables import Table


class TableSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "table_sessions"

    table_id: Mapped[UUID] = mapped_column(
        ForeignKey("tables.id"), nullable=False, index=True
    )

    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)

    token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    table: Mapped[Optional["Table"]] = relationship("Table")

    __table_args__ = ({"schema": "tenant"},)
