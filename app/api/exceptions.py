"""Exceptions API endpoints."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.models import ExceptionResponse, SourceEvidence
from app.db import session_scope
from app.models.exception import Exception as ExceptionModel
from app.models.exception_evidence import ExceptionEvidence
from app.models.task import Task

router = APIRouter()


def exception_to_response(
    exc: ExceptionModel,
    evidence: list[ExceptionEvidence],
) -> ExceptionResponse:
    """Serialize a persisted exception row and its source evidence links."""
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


@router.get("", response_model=list[ExceptionResponse], tags=["Exceptions"])
async def list_exceptions(
    status: str | None = Query(None, description="Filter by status"),
    assignee_id: str | None = Query(None, description="Filter by assignee"),
):
    """
    List persisted exceptions with their source evidence.

    Optional filters:
    - status: exception status (e.g. OPEN, RESOLVED)
    - assignee_id: employee id currently assigned to a task for the exception
    """
    with session_scope() as session:
        query = session.query(ExceptionModel)

        if status:
            query = query.filter(ExceptionModel.status == status)

        exceptions = query.order_by(ExceptionModel.business_key).all()

        results = []
        for exc in exceptions:
            if assignee_id:
                tasks = session.query(Task).filter(Task.exception_id == exc.id).all()
                if not any(t.assignee_employee_id == assignee_id for t in tasks):
                    continue

            evidence = (
                session.query(ExceptionEvidence)
                .filter(ExceptionEvidence.exception_id == exc.id)
                .all()
            )
            results.append(exception_to_response(exc, evidence))

        return results


@router.get("/{exception_id}", response_model=ExceptionResponse, tags=["Exceptions"])
async def get_exception(
    exception_id: str,
):
    """
    Get exception detail with source evidence.
    """
    with session_scope() as session:
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

        return exception_to_response(exc, evidence)
