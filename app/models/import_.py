"""Import batch and error models."""

import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now


class ImportBatch(Base):
    """Import batch tracking entity."""

    __tablename__ = "import_batches"

    id: Mapped[UUID] = mapped_column(
        "id",
        Uuid,
        primary_key=True,  # DB also defaults to uuid_generate_v4(); generate client-side
        default=uuid4,
    )
    dataset_name: Mapped[str] = mapped_column(String(100), nullable=False)
    import_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PROCESSING")
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflicting_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rule_version: Mapped[str] = mapped_column(String(50), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    errors: Mapped[list["ImportError"]] = relationship(
        "ImportError", back_populates="batch", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ImportBatch(id={self.id}, dataset={self.dataset_name}, status={self.status})>"


class ImportError(Base):
    """Row-level import error."""

    __tablename__ = "import_errors"

    id: Mapped[UUID] = mapped_column(
        "id",
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    import_batch_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("import_batches.id"),
        nullable=False,
    )
    source_record_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str] = mapped_column(String(100), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # Relationships
    batch: Mapped["ImportBatch"] = relationship("ImportBatch", back_populates="errors")

    def __repr__(self) -> str:
        return f"<ImportError(batch={self.import_batch_id}, error={self.error_code})>"
