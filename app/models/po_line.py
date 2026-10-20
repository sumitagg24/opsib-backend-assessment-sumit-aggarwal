"""Purchase order line model."""

import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class POLine(Base):
    """Purchase order line entity."""

    __tablename__ = "po_lines"

    id: Mapped[UUID] = mapped_column("id", Uuid, primary_key=True, default=uuid4)
    source_system: Mapped[str] = mapped_column(String(50), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(100), nullable=False)
    import_batch_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("import_batches.id"), nullable=False
    )
    po_number: Mapped[str] = mapped_column(String(50), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supplier_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    canonical_product_id: Mapped[str] = mapped_column(
        ForeignKey("products.canonical_product_id"), nullable=False
    )
    canonical_location_id: Mapped[str] = mapped_column(
        ForeignKey("locations.canonical_location_id"), nullable=False
    )
    ordered_qty_base: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    expected_receipt_by: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # Relationships (note: ReceiptLine references this via po_number/po_line_number, not FK)
    # receipt_lines: Mapped[list["ReceiptLine"]] = relationship(
    #     "ReceiptLine",
    #     back_populates="po_line",
    #     cascade="all, delete-orphan"
    # )

    __table_args__ = (
        UniqueConstraint("source_system", "source_record_id", name="uq_po_lines_source"),
        UniqueConstraint(
            "import_batch_id", "po_number", "line_number", name="uq_po_lines_batch_po_line"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<POLine(po={self.po_number}, line={self.line_number}, "
            f"product={self.canonical_product_id})>"
        )
