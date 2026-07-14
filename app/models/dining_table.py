import uuid
from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Integer, Boolean
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional


class DiningTable(UUIDPrimaryKeyMixin, Base):
    """Mesa física. `qr_token` es el identificador público del QR pegado en la
    mesa; el cliente lo escanea y abre una sesión anónima."""

    __tablename__ = "dining_tables"

    number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)

    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    qr_token: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = ({"schema": "tenant"},)
