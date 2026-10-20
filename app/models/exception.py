"""Exception model."""

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.exception_evidence import ExceptionEvidence
    from app.models.task import Task


class Exception(Base):
    """Business exception / finding entity."""

    __tablename__ = "exceptions"

    id: Mapped[UUID] = mapped_column("id", Uuid, primary_key=True, default=uuid4)
    exception_type: Mapped[str] = mapped_column(String(100), nullable=False)
    business_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="OPEN")
    detection_timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    po_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    po_line_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    canonical_product_id: Mapped[str] = mapped_column(
        ForeignKey("products.canonical_product_id"), nullable=False
    )
    canonical_location_id: Mapped[str] = mapped_column(
        ForeignKey("locations.canonical_location_id"), nullable=False
    )
    expected_qty_base: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    received_qty_base: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False, default=0)
    variance_base: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    base_uom: Mapped[str] = mapped_column(String(20), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(50), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(50), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    # Relationships
    evidence: Mapped[list["ExceptionEvidence"]] = relationship(
        "ExceptionEvidence", back_populates="exception", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(
        "Task", back_populates="exception", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("exception_type", "business_key", name="uq_exceptions_type_key"),
    )

    def __repr__(self) -> str:
        return f"<Exception(id={self.id}, type={self.exception_type}, po={self.po_number})>"
