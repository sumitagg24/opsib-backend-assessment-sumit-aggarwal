"""Employee model."""

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.workforce_status import WorkforceStatus


class Employee(Base):
    """Employee entity from HRMS."""

    __tablename__ = "employees"

    source_employee_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    employment_status: Mapped[str] = mapped_column(String(50), nullable=False)
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    canonical_location_id: Mapped[str | None] = mapped_column(
        ForeignKey("locations.canonical_location_id"), nullable=True
    )
    manager_employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.source_employee_id"), nullable=True
    )
    region_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
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
    workforce_status: Mapped[list["WorkforceStatus"]] = relationship(
        "WorkforceStatus", back_populates="employee", cascade="all, delete-orphan"
    )
    tasks_as_assignee: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="assignee",
        foreign_keys="Task.assignee_employee_id",
        cascade="all, delete-orphan",
    )
    tasks_as_assigner: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="assigner",
        foreign_keys="Task.assigned_by_employee_id",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Employee(id={self.source_employee_id}, name={self.display_name}, role={self.role})>"
        )
