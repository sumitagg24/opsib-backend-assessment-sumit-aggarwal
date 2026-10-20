"""Task lifecycle API endpoints."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.models import (
    AuditEventResponse,
    CompleteRequest,
    ExceptionVerificationRequest,
    ReassignRequest,
    TaskResponse,
    TaskWithAuditResponse,
)
from app.db import session_scope
from app.models.employee import Employee
from app.models.exception import Exception as ExceptionModel
from app.models.task import Task, TaskAuditEvent
from app.routing.employee_router import EmployeeRouter
from app.workflows.state_machine import StateMachineError, TaskStateMachine, TaskStatus

router = APIRouter()


class AcknowledgeResponse(BaseModel):
    task_id: str
    status: str
    message: str


class StartResponse(BaseModel):
    task_id: str
    status: str
    message: str


class CompleteResponse(BaseModel):
    task_id: str
    status: str
    resolution_code: str
    evidence_reference: str | None = None
    message: str


class ReassignResponse(BaseModel):
    task_id: str
    status: str
    assignee_employee_id: str
    assignment_reason: str
    message: str


class VerifyResponse(BaseModel):
    task_id: str
    status: str
    message: str


class ExceptionVerificationResponse(BaseModel):
    exception_id: str
    status: str
    resolved_at: str | None = None
    message: str


def get_task(task_id: str, session: Session, for_update: bool = False) -> Task:
    """Get task by ID or raise 404.

    ``for_update`` takes a row lock so concurrent workflow requests serialize
    instead of both reading the same status and racing a transition.
    """
    try:
        task_id_uuid = UUID(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid task ID format") from None

    query = session.query(Task).filter(Task.id == task_id_uuid)
    if for_update:
        query = query.with_for_update()
    task = query.first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return task


def get_task_with_audit(task_id: str, session: Session) -> tuple[Task, list[TaskAuditEvent]]:
    """Get task with its audit history."""
    task = get_task(task_id, session)
    audit_events = (
        session.query(TaskAuditEvent)
        .filter(TaskAuditEvent.task_id == task.id)
        .order_by(TaskAuditEvent.created_at)
        .all()
    )
    return task, audit_events


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    assignee_id: str | None = Query(None, description="Filter by assignee employee ID"),
    status: str | None = Query(None, description="Filter by task status"),
    exception_id: str | None = Query(None, description="Filter by exception ID"),
    location_id: str | None = Query(None, description="Filter by canonical location ID"),
):
    """
    List tasks with optional filtering.

    Can filter by assignee, status, exception, or location.
    """
    with session_scope() as session:
        query = session.query(Task)

        if assignee_id:
            query = query.filter(Task.assignee_employee_id == assignee_id)

        if status:
            query = query.filter(Task.status == status)

        if exception_id:
            try:
                query = query.filter(Task.exception_id == UUID(exception_id))
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid exception ID format") from None

        if location_id:
            # Filter tasks by exception's canonical location
            subquery = (
                session.query(Task.id)
                .join(ExceptionModel, Task.exception_id == ExceptionModel.id)
                .filter(ExceptionModel.canonical_location_id == location_id)
            )
            query = query.filter(Task.id.in_(subquery))

        tasks = query.all()

        results = []
        for task in tasks:
            results.append(
                TaskResponse(
                    id=str(task.id),
                    task_type=task.task_type,
                    exception_id=str(task.exception_id),
                    status=task.status,
                    assignee_employee_id=task.assignee_employee_id,
                    assigned_by_employee_id=task.assigned_by_employee_id,
                    assignment_reason=task.assignment_reason,
                    due_at=task.due_at.isoformat() if task.due_at else None,
                    resolution_code=task.resolution_code,
                    evidence_reference=task.evidence_reference,
                    created_at=task.created_at.isoformat() if task.created_at else None,
                    updated_at=task.updated_at.isoformat() if task.updated_at else None,
                )
            )

        return results


@router.get("/{task_id}", response_model=TaskWithAuditResponse)
async def get_task_detail(task_id: str):
    """Get task detail with audit history."""
    with session_scope() as session:
        task, audit_events = get_task_with_audit(task_id, session)

        audit_response = [
            AuditEventResponse(
                id=str(event.id),
                event_type=event.event_type,
                from_status=event.from_status,
                to_status=event.to_status,
                actor_employee_id=event.actor_employee_id,
                reason=event.reason,
                created_at=event.created_at.isoformat() if event.created_at else None,
            )
            for event in audit_events
        ]

        return TaskWithAuditResponse(
            id=str(task.id),
            task_type=task.task_type,
            exception_id=str(task.exception_id),
            status=task.status,
            assignee_employee_id=task.assignee_employee_id,
            assigned_by_employee_id=task.assigned_by_employee_id,
            assignment_reason=task.assignment_reason,
            due_at=task.due_at.isoformat() if task.due_at else None,
            resolution_code=task.resolution_code,
            evidence_reference=task.evidence_reference,
            created_at=task.created_at.isoformat() if task.created_at else None,
            updated_at=task.updated_at.isoformat() if task.updated_at else None,
            audit_events=audit_response,
        )


@router.post("/{task_id}/acknowledge", response_model=AcknowledgeResponse)
async def acknowledge_task(task_id: str):
    """
    Acknowledge a task (ASSIGNED -> ACKNOWLEDGED).

    Only the assigned employee or a manager can acknowledge.
    """
    with session_scope() as session:
        task = get_task(task_id, session, for_update=True)
        state_machine = TaskStateMachine(session)

        # Use SYSTEM as actor (in real app, would be authenticated user)
        # Note: SYSTEM is not an employee, so we pass actor=None for the FK
        try:
            audit_event = state_machine.acknowledge(
                task=task,
                actor=None,
                reason="Task acknowledged by SYSTEM",
            )
            session.add(audit_event)
            session.commit()

            return AcknowledgeResponse(
                task_id=str(task.id),
                status=task.status,
                message="Task acknowledged successfully",
            )
        except StateMachineError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{task_id}/start", response_model=StartResponse)
async def start_task(task_id: str):
    """
    Start working on a task (ACKNOWLEDGED -> IN_PROGRESS).
    """
    with session_scope() as session:
        task = get_task(task_id, session, for_update=True)
        state_machine = TaskStateMachine(session)

        # Use None for actor since SYSTEM is not an employee record
        try:
            audit_event = state_machine.start(
                task=task,
                actor=None,
                reason="Task started by SYSTEM",
            )
            session.add(audit_event)
            session.commit()

            return StartResponse(
                task_id=str(task.id),
                status=task.status,
                message="Task started successfully",
            )
        except StateMachineError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{task_id}/complete", response_model=CompleteResponse)
async def complete_task(
    task_id: str,
    request: CompleteRequest,
):
    """
    Complete a task (IN_PROGRESS -> COMPLETED).

    Requires resolution_code and optional evidence_reference.
    Safe for retries - idempotent if already completed.
    """
    with session_scope() as session:
        task = get_task(task_id, session, for_update=True)
        state_machine = TaskStateMachine(session)

        # Use None for actor since SYSTEM is not an employee record
        try:
            audit_event = state_machine.complete(
                task=task,
                actor=None,
                resolution_code=request.resolution_code,
                evidence_reference=request.evidence_reference,
            )
            session.add(audit_event)
            session.commit()

            # If already completed (idempotent retry), return current state
            return CompleteResponse(
                task_id=str(task.id),
                status=task.status,
                resolution_code=task.resolution_code or request.resolution_code,
                evidence_reference=task.evidence_reference or request.evidence_reference,
                message="Task completed successfully"
                if audit_event.event_type == "COMPLETED"
                else "Task was already completed (idempotent retry)",
            )
        except StateMachineError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{task_id}/reassign", response_model=ReassignResponse)
async def reassign_task(
    task_id: str,
    request: ReassignRequest,
):
    """
    Reassign a task to a different employee.

    Requires new assignee employee ID and reason.
    Validates that new assignee exists and is eligible.
    """
    with session_scope() as session:
        task = get_task(task_id, session, for_update=True)

        # Validate new assignee exists
        new_assignee = (
            session.query(Employee)
            .filter(Employee.source_employee_id == request.assignee_employee_id)
            .first()
        )

        if not new_assignee:
            raise HTTPException(
                status_code=400, detail=f"Employee {request.assignee_employee_id} not found"
            )

        # Employment status is not negotiable: an INACTIVE employee must never
        # receive work, even through a manual reassignment.
        if new_assignee.employment_status != "ACTIVE":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Employee {request.assignee_employee_id} is "
                    f"{new_assignee.employment_status}, not ACTIVE"
                ),
            )

        actor_id = request.actor_employee_id
        if actor_id:
            actor = session.query(Employee).filter(Employee.source_employee_id == actor_id).first()
            if not actor:
                raise HTTPException(
                    status_code=400,
                    detail=f"Actor employee {actor_id} not found",
                )

        # A manual reassignment is still an assignment: enforce the same
        # location, role, employment and shift policy as automatic routing.
        exception = task.exception
        if exception is None:
            raise HTTPException(status_code=400, detail="Task has no associated exception")

        if not EmployeeRouter(session).is_eligible_for_exception(new_assignee, exception):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Employee {request.assignee_employee_id} is not eligible for assignment "
                    f"at {exception.canonical_location_id}: requires ACTIVE employment, a "
                    f"fresh ON_SHIFT observation, and an approved role at the location"
                ),
            )

        state_machine = TaskStateMachine(session)

        try:
            reassign_result, _ = state_machine.reassign(
                task=task,
                new_assignee=request.assignee_employee_id,
                actor=actor_id,
                reason=request.reason,
            )
            session.add(reassign_result)
            session.commit()

            return ReassignResponse(
                task_id=str(task.id),
                status=task.status,
                assignee_employee_id=task.assignee_employee_id,
                assignment_reason=task.assignment_reason,
                message="Task reassigned successfully",
            )
        except StateMachineError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{task_id}/verify", response_model=VerifyResponse)
async def verify_task(task_id: str):
    """
    Verify a completed task (PENDING_VERIFICATION -> VERIFIED).

    Only completed tasks can be verified.
    """
    with session_scope() as session:
        task = get_task(task_id, session, for_update=True)
        state_machine = TaskStateMachine(session)

        # Use None for actor since SYSTEM is not an employee record
        try:
            # First ensure task is in PENDING_VERIFICATION state
            if task.status != TaskStatus.PENDING_VERIFICATION.value:
                # Try to transition to PENDING_VERIFICATION first
                if task.status == TaskStatus.COMPLETED.value:
                    state_machine.validate_transition(
                        task,
                        TaskStatus.PENDING_VERIFICATION,
                        actor=None,
                        reason="Awaiting verification by SYSTEM",
                    )
                else:
                    raise StateMachineError(
                        f"Cannot verify task in status {task.status}. "
                        f"Task must be in PENDING_VERIFICATION status."
                    )

            audit_event = state_machine.validate_transition(
                task,
                TaskStatus.VERIFIED,
                actor=None,
                reason="Task verified by SYSTEM",
            )
            session.add(audit_event)
            session.commit()

            return VerifyResponse(
                task_id=str(task.id),
                status=task.status,
                message="Task verified successfully",
            )
        except StateMachineError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/exceptions/{exception_id}/verify", response_model=ExceptionVerificationResponse)
async def verify_exception(
    exception_id: str,
    request: ExceptionVerificationRequest = None,
):
    """
    Verify an exception (mark as resolved).

    All tasks for this exception must be verified before the exception can be resolved.
    """
    with session_scope() as session:
        try:
            exc_id = UUID(exception_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid exception ID format") from None

        exception = session.query(ExceptionModel).filter(ExceptionModel.id == exc_id).first()

        if not exception:
            raise HTTPException(status_code=404, detail=f"Exception {exception_id} not found")

        # Check if all tasks are verified
        tasks = session.query(Task).filter(Task.exception_id == exc_id).all()

        if not tasks:
            raise HTTPException(status_code=400, detail="Cannot verify exception with no tasks")

        unverified_tasks = [t for t in tasks if t.status != TaskStatus.VERIFIED.value]

        if unverified_tasks:
            task_ids = [str(t.id) for t in unverified_tasks]
            raise HTTPException(
                status_code=400,
                detail=f"Cannot verify exception until all tasks are verified. "
                f"Unverified tasks: {', '.join(task_ids)}",
            )

        # Verify the exception
        if exception.status == "RESOLVED":
            return ExceptionVerificationResponse(
                exception_id=str(exception.id),
                status=exception.status,
                resolved_at=exception.resolved_at.isoformat() if exception.resolved_at else None,
                message="Exception is already resolved",
            )

        # Update exception status
        exception.status = "RESOLVED"
        exception.resolution_code = (
            request.resolution_code if request and request.resolution_code else "VERIFIED"
        )
        # resolved_at has no default in the schema, so set it explicitly; leaving it
        # NULL would make a resolved exception indistinguishable from a stale one.
        exception.resolved_at = datetime.now(UTC)

        session.commit()

        return ExceptionVerificationResponse(
            exception_id=str(exception.id),
            status=exception.status,
            resolved_at=exception.resolved_at.isoformat() if exception.resolved_at else None,
            message="Exception verified and resolved successfully",
        )


@router.post("/{task_id}/pending_verification", response_model=VerifyResponse)
async def request_verification(task_id: str):
    """
    Move task to pending verification state (COMPLETED -> PENDING_VERIFICATION).

    Called after task completion to request verification.
    """
    with session_scope() as session:
        task = get_task(task_id, session, for_update=True)
        state_machine = TaskStateMachine(session)

        # Use None for actor since SYSTEM is not an employee record
        try:
            audit_event = state_machine.validate_transition(
                task,
                TaskStatus.PENDING_VERIFICATION,
                actor=None,
                reason="Awaiting verification by SYSTEM",
            )
            session.add(audit_event)
            session.commit()

            return VerifyResponse(
                task_id=str(task.id),
                status=task.status,
                message="Task moved to pending verification",
            )
        except StateMachineError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
