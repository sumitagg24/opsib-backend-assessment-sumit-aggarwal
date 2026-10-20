"""Reconciliation API endpoints - actual implementation."""

from decimal import Decimal

from dateutil.parser import parse as parse_datetime
from fastapi import APIRouter, HTTPException, Query

from app.api.models import (
    ExceptionResponse,
    ReconciliationResponse,
    SourceEvidence,
)
from app.config import settings
from app.db import session_scope
from app.detection.shortfall_detector import ShortfallDetector
from app.models.exception import Exception as ExceptionModel
from app.models.exception_evidence import ExceptionEvidence
from app.models.task import Task
from app.routing.employee_router import EmployeeRouter
from app.workflows.state_machine import TaskStateMachine, TaskStatus

router = APIRouter()


@router.post("/run", response_model=ReconciliationResponse)
async def run_reconciliation(
    evaluation_time: str | None = Query(
        None,
        alias="evaluation_time",
        description="Override frozen evaluation time (ISO8601 format)",
    ),
    import_batch_id: str | None = Query(
        None, alias="import_batch_id", description="Filter to specific import batch (optional)"
    ),
):
    """
    Run reconciliation to detect receipt shortfalls.

    Uses the frozen evaluation time from settings unless overridden.
    Optionally filters to a specific import batch.
    """
    # Parse evaluation time
    if evaluation_time:
        try:
            eval_dt = parse_datetime(evaluation_time)
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"Invalid evaluation_time format: {e}"
            ) from e
    else:
        eval_dt = parse_datetime(settings.frozen_evaluation_time)

    # Run detection
    with session_scope() as session:
        detector = ShortfallDetector(
            session=session,
            rule_version="1.0",
            mapping_version="1.0",
            evaluation_time=eval_dt,
            tolerance=Decimal("0"),
        )

        # Filter by import batch if specified
        if import_batch_id:
            from uuid import UUID

            try:
                batch_uuid = UUID(import_batch_id)
            except (ValueError, AttributeError, TypeError):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid import_batch_id format",
                ) from None
            detector.run_detection_on_fixtures(batch_uuid)
        else:
            detector.detect_shortfalls()

        # ``evaluated_exceptions`` contains every shortfall, whether this run
        # inserted it or found an existing row. Tasks must be reconciled for
        # both, so a task missing from an earlier partial run is still created.
        shortfall_exceptions = detector.evaluated_exceptions
        created_count = len(detector.created_exceptions)

        # Create tasks for exceptions
        employee_router = EmployeeRouter(session, evaluation_time=eval_dt)
        state_machine = TaskStateMachine(session)

        for exception in shortfall_exceptions:
            # Only create task if not already created
            existing_task = (
                session.query(Task)
                .filter(
                    Task.exception_id == exception.id,
                    Task.task_type == "INVESTIGATE_SHORTFALL",
                )
                .first()
            )

            if not existing_task:
                # Find eligible assignee
                assignee, reason, error = employee_router.find_eligible_assignee(
                    exception,
                    preferred_role="receiving_associate",
                    fallback_roles=["store_manager"],
                )

                # Create task. When nobody is eligible the task stays CREATED
                # and visible for manual assignment; persist the routing failure
                # as the assignment reason so the queue is explainable.
                assignment_reason = reason if assignee else error

                # Create task
                from uuid import uuid4

                task = Task(
                    id=uuid4(),
                    task_type="INVESTIGATE_SHORTFALL",
                    exception_id=exception.id,
                    status=TaskStatus.CREATED.value,
                    assignee_employee_id=assignee.source_employee_id if assignee else None,
                    # SYSTEM is not an employee row, and tasks.assigned_by_employee_id
                    # is a foreign key to employees: a system-initiated assignment must
                    # leave the assigner NULL instead of writing a non-existent id.
                    assigned_by_employee_id=None,
                    assignment_reason=assignment_reason,
                )
                session.add(task)

                # If assignee found, transition to ASSIGNED
                if assignee:
                    state_machine.validate_transition(
                        task,
                        TaskStatus.ASSIGNED,
                        actor=None,
                        reason=reason,
                    )

        session.commit()

        # Get all exceptions with evidence
        all_exceptions = session.query(ExceptionModel).all()

        exception_responses = []
        for exc in all_exceptions:
            evidence = (
                session.query(ExceptionEvidence)
                .filter(ExceptionEvidence.exception_id == exc.id)
                .all()
            )

            exception_responses.append(
                ExceptionResponse(
                    id=str(exc.id),
                    exception_type=exc.exception_type,
                    business_key=exc.business_key,
                    status=exc.status,
                    detection_timestamp=exc.detection_timestamp.isoformat()
                    if exc.detection_timestamp
                    else None,
                    po_number=exc.po_number,
                    po_line_number=exc.po_line_number,
                    canonical_product_id=exc.canonical_product_id,
                    canonical_location_id=exc.canonical_location_id,
                    expected_qty_base=float(exc.expected_qty_base),
                    received_qty_base=float(exc.received_qty_base),
                    variance_base=float(exc.variance_base),
                    base_uom=exc.base_uom,
                    rule_version=exc.rule_version,
                    mapping_version=exc.mapping_version,
                    explanation=exc.explanation,
                    source_evidence=[
                        SourceEvidence(
                            source_system=e.source_system,
                            source_record_id=e.source_record_id,
                            record_type=e.record_type,
                        )
                        for e in evidence
                    ],
                    resolution_code=exc.resolution_code,
                    resolved_at=exc.resolved_at.isoformat() if exc.resolved_at else None,
                )
            )

    return ReconciliationResponse(
        evaluation_time=eval_dt.isoformat(),
        rule_version="1.0",
        mapping_version="1.0",
        total_po_lines_evaluated=detector.evaluated_po_lines,
        exceptions_created=created_count,
        exceptions=exception_responses,
    )


@router.get("", response_model=list[ExceptionResponse])
async def list_exceptions(
    status: str | None = Query(None, description="Filter by status"),
    po_number: str | None = Query(None, description="Filter by PO number"),
    assignee_id: str | None = Query(None, description="Filter by assignee"),
):
    """List exceptions with optional filtering."""
    with session_scope() as session:
        query = session.query(ExceptionModel)

        if status:
            query = query.filter(ExceptionModel.status == status)

        if po_number:
            query = query.filter(ExceptionModel.po_number == po_number)

        exceptions = query.all()

        results = []
        for exc in exceptions:
            evidence = (
                session.query(ExceptionEvidence)
                .filter(ExceptionEvidence.exception_id == exc.id)
                .all()
            )

            # Check for tasks
            tasks = session.query(Task).filter(Task.exception_id == exc.id).all()
            assignee_id_result = tasks[0].assignee_employee_id if tasks else None

            if assignee_id and assignee_id_result != assignee_id:
                continue

            results.append(
                ExceptionResponse(
                    id=str(exc.id),
                    exception_type=exc.exception_type,
                    business_key=exc.business_key,
                    status=exc.status,
                    detection_timestamp=exc.detection_timestamp.isoformat()
                    if exc.detection_timestamp
                    else None,
                    po_number=exc.po_number,
                    po_line_number=exc.po_line_number,
                    canonical_product_id=exc.canonical_product_id,
                    canonical_location_id=exc.canonical_location_id,
                    expected_qty_base=float(exc.expected_qty_base),
                    received_qty_base=float(exc.received_qty_base),
                    variance_base=float(exc.variance_base),
                    base_uom=exc.base_uom,
                    rule_version=exc.rule_version,
                    mapping_version=exc.mapping_version,
                    explanation=exc.explanation,
                    source_evidence=[
                        SourceEvidence(
                            source_system=e.source_system,
                            source_record_id=e.source_record_id,
                            record_type=e.record_type,
                        )
                        for e in evidence
                    ],
                    resolution_code=exc.resolution_code,
                    resolved_at=exc.resolved_at.isoformat() if exc.resolved_at else None,
                )
            )

        return results


@router.get("/{exception_id}", response_model=ExceptionResponse)
async def get_exception(exception_id: str):
    """Get exception detail with source evidence."""
    with session_scope() as session:
        from uuid import UUID

        try:
            exc_id = UUID(exception_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid exception ID format") from None

        exc = session.query(ExceptionModel).filter(ExceptionModel.id == exc_id).first()

        if not exc:
            raise HTTPException(status_code=404, detail=f"Exception {exception_id} not found")

        evidence = (
            session.query(ExceptionEvidence).filter(ExceptionEvidence.exception_id == exc.id).all()
        )

        return ExceptionResponse(
            id=str(exc.id),
            exception_type=exc.exception_type,
            business_key=exc.business_key,
            status=exc.status,
            detection_timestamp=exc.detection_timestamp.isoformat()
            if exc.detection_timestamp
            else None,
            po_number=exc.po_number,
            po_line_number=exc.po_line_number,
            canonical_product_id=exc.canonical_product_id,
            canonical_location_id=exc.canonical_location_id,
            expected_qty_base=float(exc.expected_qty_base),
            received_qty_base=float(exc.received_qty_base),
            variance_base=float(exc.variance_base),
            base_uom=exc.base_uom,
            rule_version=exc.rule_version,
            mapping_version=exc.mapping_version,
            explanation=exc.explanation,
            source_evidence=[
                SourceEvidence(
                    source_system=e.source_system,
                    source_record_id=e.source_record_id,
                    record_type=e.record_type,
                )
                for e in evidence
            ],
            resolution_code=exc.resolution_code,
            resolved_at=exc.resolved_at.isoformat() if exc.resolved_at else None,
        )
