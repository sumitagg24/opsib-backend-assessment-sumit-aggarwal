"""Exception evidence model."""

import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now


class ExceptionEvidence(Base):
    """Links exception to source records."""

    __tablename__ = "exception_evidence"

    id: Mapped[UUID] = mapped_column("id", Uuid, primary_key=True, default=uuid4)
    exception_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("exceptions.id"), nullable=False)
    source_system: Mapped[str] = mapped_column(String(50), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(100), nullable=False)
    record_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # Relationships
    exception: Mapped["Exception"] = relationship("Exception", back_populates="evidence")

    __table_args__ = (
        UniqueConstraint(
            "exception_id", "source_system", "source_record_id", name="uq_exception_evidence"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ExceptionEvidence(exception={self.exception_id}, "
            f"source={self.source_system}:{self.source_record_id})>"
        )
