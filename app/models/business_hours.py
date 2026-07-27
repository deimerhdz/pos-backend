from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy import Integer, Time, Boolean, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional
from datetime import time


class BusinessHours(UUIDPrimaryKeyMixin, Base):
    """Horario de atención por día de la semana (RF-073). `day_of_week` 0=lunes..
    6=domingo. `closed=True` marca el día cerrado (open/close se ignoran)."""

    __tablename__ = "business_hours"

    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)

    open_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    close_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)

    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_business_hours_dow"),
        UniqueConstraint("day_of_week", name="uq__business_hours__day_of_week"),
        {"schema": "tenant"},
    )
