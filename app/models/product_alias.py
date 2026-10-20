"""Product alias model."""

import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.product import Product


class ProductAlias(Base):
    """Product alias mapping from source system to canonical product."""

    __tablename__ = "product_aliases"

    id: Mapped[UUID] = mapped_column("id", Uuid, primary_key=True, default=uuid4)
    source_system: Mapped[str] = mapped_column(String(50), nullable=False)
    external_item_id: Mapped[str] = mapped_column(String(100), nullable=False)
    canonical_product_id: Mapped[str] = mapped_column(
        ForeignKey("products.canonical_product_id"), nullable=False
    )
    source_uom: Mapped[str] = mapped_column(String(20), nullable=False)
    base_uom: Mapped[str] = mapped_column(String(20), nullable=False)
    to_base_factor: Mapped[float] = mapped_column(Numeric(15, 4), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # Relationships
    canonical_product: Mapped["Product"] = relationship("Product")

    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "external_item_id",
            "mapping_version",
            name="uq_product_aliases_source_external_version",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ProductAlias({self.source_system}:{self.external_item_id} "
            f"-> {self.canonical_product_id})>"
        )
