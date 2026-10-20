"""Receipt line model."""

import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class ReceiptLine(Base):
    """Goods receipt line entity."""

    __tablename__ = "receipt_lines"

    id: Mapped[UUID] = mapped_column("id", Uuid, primary_key=True, default=uuid4)
    source_system: Mapped[str] = mapped_column(String(50), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(100), nullable=False)
    import_batch_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("import_batches.id"), nullable=False
    )
    receipt_number: Mapped[str] = mapped_column(String(50), nullable=False)
    receipt_line: Mapped[int] = mapped_column(Integer, nullable=False)
    po_number: Mapped[str] = mapped_column(String(50), nullable=False)
    po_line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_product_id: Mapped[str] = mapped_column(
        ForeignKey("products.canonical_product_id"), nullable=False
    )
    canonical_location_id: Mapped[str] = mapped_column(
        ForeignKey("locations.canonical_location_id"), nullable=False
    )
    accepted_qty_base: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    posting_status: Mapped[str] = mapped_column(String(50), nullable=False)
    received_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # Relationships
    # Note: po_number and po_line_number reference a POLine but without FK constraint
    # Relationships (commented out - would need proper FK for SQLAlchemy relationship)
    # po_line: Mapped["POLine | None"] = relationship("POLine", back_populates="receipt_lines")

    __table_args__ = (
        UniqueConstraint("source_system", "source_record_id", name="uq_receipt_lines_source"),
        UniqueConstraint(
            "import_batch_id",
            "receipt_number",
            "receipt_line",
            name="uq_receipt_lines_batch_receipt_line",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ReceiptLine(receipt={self.receipt_number}, "
            f"line={self.receipt_line}, qty={self.accepted_qty_base})>"
        )
