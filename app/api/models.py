"""API request/response models."""

from datetime import datetime

from pydantic import BaseModel, Field


# Health
class HealthResponse(BaseModel):
    status: str
    database: str
    frozen_evaluation_time: str
    service: str
    version: str


# Imports
class ImportStartResponse(BaseModel):
    import_id: str
    dataset: str
    status: str
    message: str


class ImportError(BaseModel):
    id: str
    source_record_id: str | None = None
    row_number: int | None = None
    error_code: str
    error_message: str
    raw_data: dict | None = None


class ImportBatchResponse(BaseModel):
    id: str
    dataset_name: str
    import_reference: str
    status: str
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    conflicting_rows: int
    rule_version: str
    mapping_version: str
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    errors: list[ImportError] = []


class DataQualityResponse(BaseModel):
    unresolved_product_mappings: list[dict] = []
    unresolved_location_mappings: list[dict] = []
    rejected_records: list[ImportError] = []
    conflicting_records: list[dict] = []


# Reconciliation
class ReconciliationRequest(BaseModel):
    evaluation_time: str | None = None


class SourceEvidence(BaseModel):
    source_system: str
    source_record_id: str
    record_type: str


class ExceptionResponse(BaseModel):
    id: str
    exception_type: str
    business_key: str
    status: str
    detection_timestamp: datetime
    po_number: str | None = None
    po_line_number: int | None = None
    canonical_product_id: str
    canonical_location_id: str
    expected_qty_base: float
    received_qty_base: float
    variance_base: float
    base_uom: str
    rule_version: str
    mapping_version: str
    explanation: str
    source_evidence: list[SourceEvidence] = []
    resolution_code: str | None = None
    resolved_at: datetime | None = None


class ReconciliationResponse(BaseModel):
    evaluation_time: datetime
    rule_version: str
    mapping_version: str
    total_po_lines_evaluated: int
    exceptions_created: int
    exceptions: list[ExceptionResponse] = []


# Tasks
class TaskResponse(BaseModel):
    id: str
    task_type: str
    exception_id: str
    status: str
    assignee_employee_id: str | None = None
    assigned_by_employee_id: str | None = None
    assignment_reason: str | None = None
    due_at: datetime | None = None
    resolution_code: str | None = None
    evidence_reference: str | None = None
    created_at: datetime
    updated_at: datetime


class AuditEventResponse(BaseModel):
    id: str
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    actor_employee_id: str | None = None
    reason: str | None = None
    created_at: datetime


class ReassignRequest(BaseModel):
    assignee_employee_id: str
    reason: str = Field(min_length=1)
    # Who is making the change. Optional because the service has no
    # authentication layer; when supplied it must reference a real employee and
    # is stored on both the task and the audit event.
    actor_employee_id: str | None = None


class CompleteRequest(BaseModel):
    resolution_code: str
    evidence_reference: str | None = None


class ExceptionVerificationRequest(BaseModel):
    resolution_code: str | None = None
    evidence_reference: str | None = None


class TaskWithAuditResponse(TaskResponse):
    audit_events: list[AuditEventResponse] = []
