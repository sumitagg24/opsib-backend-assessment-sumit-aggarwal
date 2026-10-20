"""Task state machine for workflow enforcement."""

from enum import StrEnum

from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models.task import Task, TaskAuditEvent


class TaskStatus(StrEnum):
    """Task status values."""

    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    REASSIGNED = "REASSIGNED"
    REOPENED = "REOPENED"


# Define valid transitions
VALID_TRANSITIONS = {
    TaskStatus.CREATED: [TaskStatus.ASSIGNED, TaskStatus.REJECTED, TaskStatus.BLOCKED],
    TaskStatus.ASSIGNED: [TaskStatus.ACKNOWLEDGED, TaskStatus.BLOCKED, TaskStatus.REASSIGNED],
    TaskStatus.ACKNOWLEDGED: [TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED],
    TaskStatus.IN_PROGRESS: [TaskStatus.COMPLETED, TaskStatus.BLOCKED],
    TaskStatus.COMPLETED: [TaskStatus.PENDING_VERIFICATION],
    TaskStatus.PENDING_VERIFICATION: [TaskStatus.VERIFIED, TaskStatus.REOPENED],
    TaskStatus.VERIFIED: [],  # Terminal state
    TaskStatus.REJECTED: [],  # Terminal state
    TaskStatus.BLOCKED: [TaskStatus.ASSIGNED, TaskStatus.ACKNOWLEDGED, TaskStatus.IN_PROGRESS],
    # REASSIGNED is a special case - handled separately
}


class StateMachineError(Exception):
    """Raised when an invalid state transition is attempted."""

    pass


class TaskStateMachine:
    """Server-enforced task state machine."""

    def __init__(self, session: Session):
        """
        Initialize the state machine.

        Args:
            session: Database session
        """
        self.session = session

    def validate_transition(
        self,
        task: Task,
        new_status: TaskStatus,
        actor: str | None = None,
        reason: str | None = None,
    ) -> TaskAuditEvent:
        """
        Validate and execute a state transition.

        Args:
            task: Task to transition
            new_status: New status to transition to
            actor: Employee ID of the actor making the change
            reason: Optional reason for the transition

        Returns:
            Created audit event

        Raises:
            StateMachineError: If transition is invalid
        """
        current_status = TaskStatus(task.status)
        new_status_enum = TaskStatus(new_status) if isinstance(new_status, str) else new_status

        # Check if transition is valid
        if (
            new_status_enum not in VALID_TRANSITIONS.get(current_status, [])
            and new_status_enum != TaskStatus.REASSIGNED
        ):
            valid = [s.value for s in VALID_TRANSITIONS.get(current_status, [])]
            raise StateMachineError(
                f"Invalid transition from {current_status.value} to {new_status_enum.value}. "
                f"Valid transitions: {valid}"
            )

        # Update task status
        task.status = new_status_enum.value
        task.updated_at = utc_now()

        # Create audit event with task already having its ID
        audit_event = TaskAuditEvent(
            task_id=task.id,
            event_type=self._get_event_type(current_status, new_status_enum),
            from_status=current_status.value,
            to_status=new_status_enum.value,
            actor_employee_id=actor,
            reason=reason,
            created_at=utc_now(),
        )

        self.session.add(audit_event)
        self.session.add(task)

        return audit_event

    def acknowledge(
        self,
        task: Task,
        actor: str,
        reason: str | None = None,
    ) -> TaskAuditEvent:
        """
        Acknowledge a task (ASSIGNED -> ACKNOWLEDGED).

        Args:
            task: Task to acknowledge
            actor: Employee ID of the actor
            reason: Optional reason

        Returns:
            Created audit event
        """
        return self.validate_transition(
            task,
            TaskStatus.ACKNOWLEDGED,
            actor=actor,
            reason=reason or "Task acknowledged",
        )

    def start(
        self,
        task: Task,
        actor: str,
        reason: str | None = None,
    ) -> TaskAuditEvent:
        """
        Start working on a task (ACKNOWLEDGED -> IN_PROGRESS).

        Args:
            task: Task to start
            actor: Employee ID of the actor
            reason: Optional reason

        Returns:
            Created audit event
        """
        return self.validate_transition(
            task,
            TaskStatus.IN_PROGRESS,
            actor=actor,
            reason=reason or "Task started",
        )

    def complete(
        self,
        task: Task,
        actor: str,
        resolution_code: str,
        evidence_reference: str | None = None,
    ) -> TaskAuditEvent:
        """
        Complete a task (IN_PROGRESS -> COMPLETED).
        Safe for retries - idempotent if already completed.

        Args:
            task: Task to complete
            actor: Employee ID of the actor
            resolution_code: Resolution code for the completion
            evidence_reference: Optional reference to evidence

        Returns:
            Created audit event
        """
        # Completion is idempotent once the work is done. A retry may arrive
        # after the task has already moved on to PENDING_VERIFICATION, so both
        # post-completion states are accepted without changing the row.
        if task.status in (
            TaskStatus.COMPLETED.value,
            TaskStatus.PENDING_VERIFICATION.value,
        ):
            return TaskAuditEvent(
                task_id=task.id,
                event_type="COMPLETION_RETRY",
                from_status=task.status,
                to_status=task.status,
                actor_employee_id=actor,
                reason=f"Completion retry - already completed with code {task.resolution_code}",
                created_at=utc_now(),
            )

        # A verified task is terminal; completing it again is a client error,
        # not a silent success.
        if task.status == TaskStatus.VERIFIED.value:
            raise StateMachineError(
                "Cannot complete a VERIFIED task. VERIFIED is a terminal state."
            )

        # Update task with completion details
        task.resolution_code = resolution_code
        task.evidence_reference = evidence_reference

        return self.validate_transition(
            task,
            TaskStatus.COMPLETED,
            actor=actor,
            reason=f"Task completed with resolution: {resolution_code}",
        )

    def reassign(
        self,
        task: Task,
        new_assignee: str,
        actor: str,
        reason: str,
    ) -> tuple[TaskAuditEvent, TaskAuditEvent]:
        """
        Reassign a task to a new assignee.

        Args:
            task: Task to reassign
            new_assignee: New assignee employee ID
            actor: Employee ID of the actor making the change
            reason: Reason for reassignment

        Returns:
            Tuple of (assignment audit event, status change audit event)

        Raises:
            StateMachineError: If the task is in a terminal/post-completion state.
        """
        # Reassignment changes ownership, not business state. It is only legal
        # while the task is still actionable; completed or verified work must be
        # reopened through the explicit state machine, not silently reassigned.
        reassignable = {
            TaskStatus.CREATED.value,
            TaskStatus.ASSIGNED.value,
            TaskStatus.ACKNOWLEDGED.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.BLOCKED.value,
            TaskStatus.REOPENED.value,
        }
        if task.status not in reassignable:
            raise StateMachineError(
                f"Cannot reassign a task in status {task.status}. "
                f"Reassignable statuses: {', '.join(sorted(reassignable))}"
            )

        # If status is CREATED, just assign
        if task.status == TaskStatus.CREATED.value:
            task.assignee_employee_id = new_assignee
            task.assigned_by_employee_id = actor
            task.assignment_reason = reason

            # Create assignment audit event
            assignment_event = TaskAuditEvent(
                task_id=task.id,
                event_type="ASSIGNED",
                from_status=None,
                to_status=TaskStatus.ASSIGNED.value,
                actor_employee_id=actor,
                reason=reason,
                created_at=utc_now(),
            )

            # Update status to ASSIGNED
            status_event = self.validate_transition(
                task,
                TaskStatus.ASSIGNED,
                actor=actor,
                reason=f"Task assigned to {new_assignee}",
            )

            self.session.add(assignment_event)

            return (assignment_event, status_event)

        # For other statuses, record the reassignment
        old_assignee = task.assignee_employee_id

        task.assignee_employee_id = new_assignee
        task.assigned_by_employee_id = actor
        task.assignment_reason = reason
        task.updated_at = utc_now()

        # Create reassignment audit event
        reassignment_event = TaskAuditEvent(
            task_id=task.id,
            event_type="REASSIGNED",
            from_status=old_assignee,
            to_status=new_assignee,
            actor_employee_id=actor,
            reason=reason,
            created_at=utc_now(),
        )

        self.session.add(reassignment_event)

        return (reassignment_event, reassignment_event)  # Return same event twice for compatibility

    def _get_event_type(
        self,
        from_status: TaskStatus,
        to_status: TaskStatus,
    ) -> str:
        """Get the event type name for a transition."""
        # Map status pairs to event types
        event_types = {
            (TaskStatus.CREATED, TaskStatus.ASSIGNED): "ASSIGNED",
            (TaskStatus.ASSIGNED, TaskStatus.ACKNOWLEDGED): "ACKNOWLEDGED",
            (TaskStatus.ACKNOWLEDGED, TaskStatus.IN_PROGRESS): "STARTED",
            (TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED): "COMPLETED",
            (TaskStatus.COMPLETED, TaskStatus.PENDING_VERIFICATION): "COMPLETION_RECORDED",
            (TaskStatus.PENDING_VERIFICATION, TaskStatus.VERIFIED): "VERIFIED",
            (TaskStatus.PENDING_VERIFICATION, TaskStatus.REOPENED): "REOPENED",
            (TaskStatus.REJECTED,): "REJECTED",
            (TaskStatus.BLOCKED,): "BLOCKED",
        }

        # Check for direct match
        key = (from_status, to_status)
        if key in event_types:
            return event_types[key]

        # Check for single-status events
        if (from_status,) in event_types:
            return event_types[(from_status,)]

        # Default to status change
        return f"STATUS_CHANGE:{from_status.value}_TO_{to_status.value}"
