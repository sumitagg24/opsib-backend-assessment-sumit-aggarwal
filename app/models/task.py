"""Task model."""

import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.employee import Employee
    from app.models.exception import Exception


class Task(Base):
    """Task entity for workflow."""

    __tablename__ = "tasks"

    id: Mapped[UUID] = mapped_column("id", Uuid, primary_key=True, default=uuid4)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    exception_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("exceptions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="CREATED")
    assignee_employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.source_employee_id"), nullable=True
    )
    assigned_by_employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.source_employee_id"), nullable=True
    )
    assignment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    evidence_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    exception: Mapped["Exception"] = relationship("Exception", back_populates="tasks")
    assignee: Mapped["Employee | None"] = relationship(
        "Employee", back_populates="tasks_as_assignee", foreign_keys=[assignee_employee_id]
    )
    assigner: Mapped["Employee | None"] = relationship(
        "Employee", back_populates="tasks_as_assigner", foreign_keys=[assigned_by_employee_id]
    )
    audit_events: Mapped[list["TaskAuditEvent"]] = relationship(
        "TaskAuditEvent", back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("exception_id", "task_type", name="uq_tasks_exception_type"),
    )

    def __repr__(self) -> str:
        return f"<Task(id={self.id}, type={self.task_type}, status={self.status})>"


class TaskAuditEvent(Base):
    """Task audit event for history tracking."""

    __tablename__ = "task_audit_events"

    id: Mapped[UUID] = mapped_column("id", Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tasks.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    actor_employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.source_employee_id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(
        # Maps to 'metadata' column in DB (reserved name in SQLAlchemy Declarative)
        JSON,
        nullable=True,
        name="metadata",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # Relationships
    task: Mapped["Task"] = relationship("Task", back_populates="audit_events")

    def __repr__(self) -> str:
        return f"<TaskAuditEvent(task={self.task_id}, event={self.event_type})>"
