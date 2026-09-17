from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy import String, Boolean
from sqlalchemy.orm import mapped_column, Mapped


class Presentation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Catálogo global de nombres de presentación reutilizables del tenant
    (spec 083): "Pequeño", "Mediano", "Grande". Espejo de `OptionGroup`
    (`app/models/option_group.py`) -- catálogo plano, sin jerarquía propia."""

    __tablename__ = "presentations"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    __table_args__ = ({"schema": "tenant"},)
