"""Workforce status model."""

import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.employee import Employee


class WorkforceStatus(Base):
    """Workforce status / availability snapshot."""

    __tablename__ = "workforce_status"

    id: Mapped[UUID] = mapped_column("id", Uuid, primary_key=True, default=uuid4)
    source_employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.source_employee_id"), nullable=False
    )
    availability_status: Mapped[str] = mapped_column(String(50), nullable=False)
    shift_start: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shift_end: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    import_batch_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("import_batches.id"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # Relationships
    employee: Mapped["Employee"] = relationship("Employee", back_populates="workforce_status")

    __table_args__ = (
        UniqueConstraint(
            "source_employee_id", "observed_at", name="uq_workforce_status_employee_observed"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<WorkforceStatus(employee={self.source_employee_id}, "
            f"status={self.availability_status})>"
        )
