
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional


class TaxLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Asocia un impuesto a un producto o a una variante (exactamente uno)."""

    __tablename__ = "tax_links"

    tax_id: Mapped[UUID] = mapped_column(ForeignKey("taxes.id"), nullable=False, index=True)

    product_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("products.id"), nullable=True, index=True
    )

    variant_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("variants.id"), nullable=True, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "(product_id IS NOT NULL)::int + (variant_id IS NOT NULL)::int = 1",
            name="ck_tax_link_single_target",
        ),
        {"schema": "tenant"},
    )
