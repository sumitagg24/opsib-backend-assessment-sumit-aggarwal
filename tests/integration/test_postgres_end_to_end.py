"""End-to-end tests against a real PostgreSQL database.

These tests drive the actual endpoint functions (imports, reconciliation,
exceptions, tasks), the ingestion handlers, the shortfall detector and the task
state machine against PostgreSQL. They are deliberately *not* mock based: the
suite in ``tests/integration/test_idempotency.py`` simulates behaviour with
plain dictionaries and the other "integration" tests mock the session, so none
of them could reproduce the defects that only appear against a real database.

Regressions covered here (all reproduced on PostgreSQL before the fix):

1. Duplicate canonical products/locations inside one import batch - the session
   runs with ``autoflush=False``, so a pending insert was invisible to the next
   row's lookup and the batch failed with a unique violation.
2. ``ImportError`` (and every other UUID primary key) failing with
   "NULL identity key" because the ORM columns had no client-side default.
3. ``employees`` import failing with a foreign key violation when a report row
   (HR-101) precedes its manager row (HR-201).
4. Reconciliation failing with ``tasks_assigned_by_employee_id_fkey`` because
   the literal "SYSTEM" was stored as the assigner.
5. A repeated reconciliation failing with a unique violation on
   ``exceptions(exception_type, business_key)`` instead of being idempotent.
6. ``/api/v1/exceptions`` returning an empty list and ``/api/v1/exceptions/{id}``
   raising 500 (unimplemented stubs).
7. ``/api/v1/tasks/exceptions/{id}/verify`` failing with an AttributeError and
   leaving ``resolved_at`` NULL after resolution.
8. ``import_batches.completed_at`` staying NULL for completed batches.
"""

import asyncio
import datetime
import uuid
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, inspect, select, text
from sqlalchemy.dialects import postgresql
from starlette.datastructures import UploadFile

from app.api.exceptions import get_exception, list_exceptions
from app.api.imports import get_data_quality, get_import, import_dataset
from app.api.models import CompleteRequest, ExceptionVerificationRequest, ReassignRequest
from app.api.reconciliation import run_reconciliation
from app.api.tasks import (
    acknowledge_task,
    complete_task,
    get_task_detail,
    list_tasks,
    reassign_task,
    request_verification,
    start_task,
    verify_exception,
    verify_task,
)
from app.main import app as fastapi_app
from app.models.exception import Exception as ExceptionModel
from app.models.exception_evidence import ExceptionEvidence
from app.models.import_ import ImportBatch
from app.models.import_ import ImportError as ImportBatchError
from app.models.location_alias import LocationAlias
from app.models.po_line import POLine
from app.models.product_alias import ProductAlias
from app.models.task import Task, TaskAuditEvent
from app.models.workforce_status import WorkforceStatus

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
DATASETS = [
    "product_aliases",
    "location_aliases",
    "employees",
    "workforce_status",
    "purchase_order_lines",
    "goods_receipt_lines",
]


def run(coro):
    """Run an endpoint coroutine from a synchronous test."""
    return asyncio.run(coro)


def upload(name: str) -> UploadFile:
    """Build the UploadFile the import endpoint expects, from a fixture file."""
    path = FIXTURES / name
    return UploadFile(file=BytesIO(path.read_bytes()), filename=path.name)


def import_all(reference: str = "pg-test") -> dict:
    """Import all six original fixture CSVs through the real endpoint."""
    results = {}
    for dataset in DATASETS:
        results[dataset] = run(
            import_dataset(
                dataset=dataset,
                file=upload(f"{dataset}.csv"),
                import_reference=reference,
            )
        )
    return results


def import_csv(dataset: str, content: str, reference: str = "pg-test"):
    """Import an in-memory CSV through the real endpoint."""
    return run(
        import_dataset(
            dataset=dataset,
            file=UploadFile(file=BytesIO(content.encode()), filename=f"{dataset}.csv"),
            import_reference=reference,
        )
    )


def counts(session) -> dict:
    """Row counts for the tables the fixtures populate."""
    from sqlalchemy import func, select

    from app.models.employee import Employee
    from app.models.location import Location
    from app.models.po_line import POLine
    from app.models.product import Product
    from app.models.receipt_line import ReceiptLine
    from app.models.workforce_status import WorkforceStatus

    def count(model):
        return session.scalar(select(func.count()).select_from(model))

    return {
        "products": count(Product),
        "locations": count(Location),
        "product_aliases": count(ProductAlias),
        "location_aliases": count(LocationAlias),
        "employees": count(Employee),
        "workforce_status": count(WorkforceStatus),
        "po_lines": count(POLine),
        "receipt_lines": count(ReceiptLine),
        "exceptions": count(ExceptionModel),
        "evidence": count(ExceptionEvidence),
        "tasks": count(Task),
        "audit_events": count(TaskAuditEvent),
        "import_errors": count(ImportBatchError),
    }


class TestFixtureImports:
    """Imports of the original six CSV files through the real endpoint."""

    def test_all_fixture_files_import_into_postgres(self, db_session):
        results = import_all()

        for dataset, response in results.items():
            assert response.status == "COMPLETED", f"{dataset}: {response.message}"

        assert results["product_aliases"].message == (
            "Import completed: 6 accepted, 0 rejected, 0 duplicates, 0 conflicts"
        )
        assert results["location_aliases"].message == (
            "Import completed: 6 accepted, 0 rejected, 0 duplicates, 0 conflicts"
        )
        assert results["employees"].message == (
            "Import completed: 6 accepted, 0 rejected, 0 duplicates, 0 conflicts"
        )
        assert results["workforce_status"].message == (
            "Import completed: 6 accepted, 0 rejected, 0 duplicates, 0 conflicts"
        )
        # The fixtures contain exactly one identical duplicate row each.
        assert results["purchase_order_lines"].message == (
            "Import completed: 6 accepted, 0 rejected, 1 duplicates, 0 conflicts"
        )
        assert results["goods_receipt_lines"].message == (
            "Import completed: 6 accepted, 0 rejected, 1 duplicates, 0 conflicts"
        )

        stored = counts(db_session)
        assert stored["products"] == 3  # SKU-001/002/003, deduplicated
        assert stored["locations"] == 2  # STORE-014 / STORE-015
        assert stored["product_aliases"] == 6
        assert stored["location_aliases"] == 6
        assert stored["employees"] == 6
        assert stored["workforce_status"] == 6
        assert stored["po_lines"] == 6  # 7 rows - 1 duplicate
        assert stored["receipt_lines"] == 6  # 7 rows - 1 duplicate
        assert stored["import_errors"] == 0

    def test_canonical_entities_are_unique_within_a_batch(self, db_session):
        """Regression 1: three aliases map to SKU-001, but only one product row."""
        from sqlalchemy import func, select

        from app.models.location import Location
        from app.models.product import Product

        import_all()

        duplicated_products = db_session.scalars(
            select(Product.canonical_product_id)
            .group_by(Product.canonical_product_id)
            .having(func.count() > 1)
        ).all()
        duplicated_locations = db_session.scalars(
            select(Location.canonical_location_id)
            .group_by(Location.canonical_location_id)
            .having(func.count() > 1)
        ).all()

        assert duplicated_products == []
        assert duplicated_locations == []

    def test_completed_batches_record_completed_at(self, db_session):
        """Regression 8: completed batches used to stay NULL forever."""
        results = import_all()

        for dataset, response in results.items():
            batch = run(get_import(response.import_id))
            assert batch.status == "COMPLETED", dataset
            assert batch.completed_at is not None, dataset

    def test_repeat_import_is_idempotent(self, db_session):
        """Re-importing the same files adds no duplicate facts."""
        import_all("first-load")
        before = counts(db_session)

        repeat = import_all("first-load")
        after = counts(db_session)

        assert before == after
        for dataset, response in repeat.items():
            expected_dups = (
                7
                if dataset
                in (
                    "purchase_order_lines",
                    "goods_receipt_lines",
                )
                else 6
            )
            assert response.message == (
                f"Import completed: 0 accepted, 0 rejected, {expected_dups} duplicates, 0 conflicts"
            ), f"{dataset}: {response.message}"

    def test_employee_manager_forward_reference(self, db_session):
        """Regression 3: HR-101 lists HR-201 as manager before HR-201 exists."""
        from sqlalchemy import select

        from app.models.employee import Employee

        import_all()

        employees = {e.source_employee_id: e for e in db_session.scalars(select(Employee)).all()}
        assert len(employees) == 6
        assert employees["HR-101"].manager_employee_id == "HR-201"
        assert employees["HR-102"].manager_employee_id == "HR-201"
        assert employees["HR-103"].manager_employee_id == "HR-201"
        assert employees["HR-201"].manager_employee_id is None
        assert employees["HR-501"].canonical_location_id is None  # region scoped

    def test_row_level_errors_are_persisted(self, db_session):
        """Regression 2: rejected rows must produce stored ImportError rows."""
        from sqlalchemy import select

        csv_content = (
            "source_employee_id,display_name,employment_status,role,"
            "location_code,manager_employee_id,region_id\n"
            "HR-900,Good Person,ACTIVE,receiving_associate,,,,\n"
            "HR-901,Bad Person,ACTIVE,chief_of_everything,,,,\n"
        )
        response = run(
            import_dataset(
                dataset="employees",
                file=UploadFile(file=BytesIO(csv_content.encode()), filename="e.csv"),
                import_reference="error-case",
            )
        )
        assert response.status == "COMPLETED"
        assert "1 accepted, 1 rejected" in response.message

        errors = db_session.scalars(select(ImportBatchError)).all()
        assert len(errors) == 1
        assert errors[0].id is not None  # regression: NULL identity key
        assert errors[0].error_code == "INVALID_ROLE"
        assert errors[0].row_number == 3

        batch = run(get_import(response.import_id))
        assert batch.rejected_rows == 1
        assert batch.accepted_rows == 1


class TestReconciliation:
    """Detection and task creation against persisted PostgreSQL records."""

    def test_expected_shortfalls_for_po_501_to_506(self, db_session):
        import_all()
        result = run(run_reconciliation(evaluation_time=None, import_batch_id=None))

        by_po = {e.po_number: e for e in result.exceptions}
        assert set(by_po) == {"PO-501", "PO-505", "PO-506"}

        assert float(by_po["PO-501"].variance_base) == 8.0  # 100 - (60 + 32)
        assert float(by_po["PO-505"].variance_base) == 24.0  # 10*12 - 96
        assert float(by_po["PO-506"].variance_base) == 10.0  # duplicate counted once
        assert by_po["PO-505"].expected_qty_base == 120.0  # BOX -> EA conversion

        # PO-502 fully received, PO-503 deadline in the future, PO-504 cancelled.
        assert "PO-502" not in by_po
        assert "PO-503" not in by_po
        assert "PO-504" not in by_po

        stored = counts(db_session)
        assert stored["exceptions"] == 3
        assert stored["evidence"] == 7

    def test_rerun_does_not_duplicate_exceptions_or_tasks(self, db_session):
        """Regression 5: second run used to hit the business-key unique index."""
        import_all()
        run(run_reconciliation(evaluation_time=None, import_batch_id=None))
        first = counts(db_session)

        run(run_reconciliation(evaluation_time=None, import_batch_id=None))
        run(run_reconciliation(evaluation_time=None, import_batch_id=None))
        second = counts(db_session)

        assert second == first
        assert second["exceptions"] == 3
        assert second["evidence"] == 7
        assert second["tasks"] == 3
        assert second["audit_events"] == 3

    def test_task_assignment_expectations(self, db_session):
        """HR-102 covers STORE-014, HR-401 covers STORE-015, nobody else."""
        from sqlalchemy import select

        import_all()
        run(run_reconciliation(evaluation_time=None, import_batch_id=None))

        tasks = run(list_tasks(assignee_id=None, status=None, exception_id=None, location_id=None))
        assert len(tasks) == 3

        by_store = {}
        for task in tasks:
            detail = run(get_task_detail(task.id))
            exception_id = detail.exception_id
            exception = run(get_exception(exception_id))
            by_store[exception.canonical_location_id] = task.assignee_employee_id
            # Regression 4: the assigner must be NULL, not the literal "SYSTEM",
            # because tasks.assigned_by_employee_id references employees.
            assert task.assigned_by_employee_id is None

        assert by_store == {"STORE-014": "HR-102", "STORE-015": "HR-401"}

        assignees = {t.assignee_employee_id for t in tasks}
        assert "HR-101" not in assignees  # off shift in workforce status
        assert "HR-103" not in assignees  # INACTIVE employee

        assigned = db_session.scalars(
            select(Task.assignee_employee_id).where(
                Task.assignee_employee_id.in_(["HR-101", "HR-103"])
            )
        ).all()
        assert assigned == []


class TestExceptionEndpoints:
    """Regression 6: these endpoints were stubs (empty list / HTTP 500)."""

    def test_list_and_detail_return_persisted_exceptions(self, db_session):
        import_all()
        run(run_reconciliation(evaluation_time=None, import_batch_id=None))

        all_open = run(list_exceptions(status="OPEN", assignee_id=None))
        assert len(all_open) == 3
        assert {e.business_key for e in all_open} == {
            "SHORTFALL:PO-501:L1",
            "SHORTFALL:PO-505:L1",
            "SHORTFALL:PO-506:L1",
        }

        resolved = run(list_exceptions(status="RESOLVED", assignee_id=None))
        assert resolved == []

        detail = run(get_exception(all_open[0].id))
        assert detail.id == all_open[0].id
        assert detail.source_evidence, "evidence links must be returned"
        assert detail.explanation

        filtered = run(list_exceptions(status=None, assignee_id="HR-102"))
        assert len(filtered) == 2

    def test_exception_endpoint_error_paths(self, db_session):
        with pytest.raises(HTTPException) as bad_format:
            run(get_exception("not-a-uuid"))
        assert bad_format.value.status_code == 400

        with pytest.raises(HTTPException) as missing:
            run(get_exception(str(uuid.uuid4())))
        assert missing.value.status_code == 404


class TestTaskLifecycle:
    """State machine behaviour against PostgreSQL."""

    def _task_for_store014(self):
        import_all()
        run(run_reconciliation(evaluation_time=None, import_batch_id=None))
        tasks = run(
            list_tasks(assignee_id="HR-102", status=None, exception_id=None, location_id=None)
        )
        assert len(tasks) == 2
        return tasks[0].id

    def test_full_lifecycle_with_idempotent_completion_retry(self, db_session):
        task_id = self._task_for_store014()

        assert run(acknowledge_task(task_id)).status == "ACKNOWLEDGED"
        assert run(start_task(task_id)).status == "IN_PROGRESS"

        first = run(
            complete_task(
                task_id,
                CompleteRequest(
                    resolution_code="SHORTFALL_CONFIRMED",
                    evidence_reference="GRN501-A",
                ),
            )
        )
        assert first.status == "COMPLETED"
        assert first.message == "Task completed successfully"

        retry = run(
            complete_task(
                task_id,
                CompleteRequest(resolution_code="SHORTFALL_CONFIRMED"),
            )
        )
        assert retry.status == "COMPLETED"
        assert retry.message == "Task was already completed (idempotent retry)"

        assert run(verify_task(task_id)).status == "VERIFIED"

        detail = run(get_task_detail(task_id))
        events = [e.event_type for e in detail.audit_events]
        assert events == [
            "ASSIGNED",
            "ACKNOWLEDGED",
            "STARTED",
            "COMPLETED",
            "COMPLETION_RETRY",
            "COMPLETION_RECORDED",
            "VERIFIED",
        ]
        assert detail.resolution_code == "SHORTFALL_CONFIRMED"

    def test_invalid_transitions_are_rejected(self, db_session):
        task_id = self._task_for_store014()

        # ASSIGNED -> COMPLETED is not allowed.
        with pytest.raises(HTTPException) as invalid:
            run(complete_task(task_id, CompleteRequest(resolution_code="SHORTFALL_CONFIRMED")))
        assert invalid.value.status_code == 400
        assert "Valid transitions: ['ACKNOWLEDGED', 'BLOCKED', 'REASSIGNED']" in (
            invalid.value.detail
        )

        # ASSIGNED -> VERIFIED is not allowed either.
        with pytest.raises(HTTPException) as cannot_verify:
            run(verify_task(task_id))
        assert cannot_verify.value.status_code == 400

        # Repeated acknowledgement is not idempotent.
        run(acknowledge_task(task_id))
        with pytest.raises(HTTPException) as again:
            run(acknowledge_task(task_id))
        assert again.value.status_code == 400

        assert run(get_task_detail(task_id)).status == "ACKNOWLEDGED"

    def test_reassignment_updates_assignee_and_audit(self, db_session):
        task_id = self._task_for_store014()

        run(
            reassign_task(
                task_id,
                ReassignRequest(assignee_employee_id="HR-201", reason="manager cover"),
            )
        )
        detail = run(get_task_detail(task_id))
        assert detail.assignee_employee_id == "HR-201"
        assert detail.audit_events[-1].event_type == "REASSIGNED"
        assert detail.audit_events[-1].from_status == "HR-102"
        assert detail.audit_events[-1].to_status == "HR-201"

        with pytest.raises(HTTPException) as unknown:
            run(
                reassign_task(
                    task_id,
                    ReassignRequest(assignee_employee_id="HR-999", reason="nope"),
                )
            )
        assert unknown.value.status_code == 400

    def test_exception_verification_sets_resolved_at(self, db_session):
        """Regression 7: verification 500'd and left resolved_at NULL."""
        import_all()
        run(run_reconciliation(evaluation_time=None, import_batch_id=None))

        tasks = run(list_tasks(assignee_id=None, status=None, exception_id=None, location_id=None))
        open_exceptions = run(list_exceptions(status="OPEN", assignee_id=None))
        target = next(e for e in open_exceptions if e.business_key == "SHORTFALL:PO-501:L1")
        task_id = next(t.id for t in tasks if t.exception_id == target.id)

        # Not all tasks verified yet -> 400.
        with pytest.raises(HTTPException) as blocked:
            run(verify_exception(target.id, ExceptionVerificationRequest(resolution_code="X")))
        assert blocked.value.status_code == 400

        run(acknowledge_task(task_id))
        run(start_task(task_id))
        run(complete_task(task_id, CompleteRequest(resolution_code="SHORTFALL_CONFIRMED")))
        run(verify_task(task_id))

        response = run(
            verify_exception(
                target.id,
                ExceptionVerificationRequest(resolution_code="SHORTFALL_CONFIRMED"),
            )
        )
        assert response.status == "RESOLVED"

        detail = run(get_exception(target.id))
        assert detail.status == "RESOLVED"
        assert detail.resolution_code == "SHORTFALL_CONFIRMED"
        assert detail.resolved_at is not None

        # A second verification is idempotent, not an error.
        repeat = run(verify_exception(target.id, ExceptionVerificationRequest(resolution_code="X")))
        assert repeat.message == "Exception is already resolved"


class TestPrimaryKeyDefaults:
    """Regression 2: every UUID primary key needs a client-side default."""

    def test_entities_get_ids_without_explicit_values(self, db_session):
        batch = ImportBatch(
            id=uuid.uuid4(),
            dataset_name="employees",
            import_reference="pk-test",
            status="PROCESSING",
            rule_version="1.0",
            mapping_version="1.0",
        )
        db_session.add(batch)
        db_session.flush()

        # Alias and exception rows reference canonical entities (foreign keys).
        from app.models.location import Location
        from app.models.product import Product

        db_session.add_all(
            [
                Product(canonical_product_id="SKU-PK", base_uom="EA"),
                Location(canonical_location_id="STORE-PK", region_id="REGION-PK"),
            ]
        )
        db_session.flush()

        error = ImportBatchError(
            import_batch_id=batch.id,
            row_number=2,
            error_code="TEST",
            error_message="row failed",
        )
        alias = ProductAlias(
            source_system="ERP",
            external_item_id="ERP-PK-TEST",
            canonical_product_id="SKU-PK",
            source_uom="EA",
            base_uom="EA",
            to_base_factor=Decimal("1"),
        )
        location_alias = LocationAlias(
            source_system="ERP",
            external_location_id="LOC-PK",
            canonical_location_id="STORE-PK",
            region_id="REGION-PK",
        )
        db_session.add_all([error, alias, location_alias])
        db_session.flush()

        exception = ExceptionModel(
            exception_type="RECEIPT_SHORTFALL",
            business_key="SHORTFALL:PK:L1",
            po_number="PK-1",
            po_line_number=1,
            canonical_product_id="SKU-PK",
            canonical_location_id="STORE-PK",
            expected_qty_base=Decimal("1"),
            received_qty_base=Decimal("0"),
            variance_base=Decimal("1"),
            base_uom="EA",
            rule_version="1.0",
            mapping_version="1.0",
            explanation="pk test",
        )
        db_session.add(exception)
        db_session.flush()

        evidence = ExceptionEvidence(
            exception_id=exception.id,
            source_system="ERP",
            source_record_id="PK-1",
            record_type="PO_LINE",
        )
        task = Task(
            task_type="INVESTIGATE_SHORTFALL",
            exception_id=exception.id,
            status="CREATED",
        )
        db_session.add_all([evidence, task])
        db_session.flush()

        audit = TaskAuditEvent(
            task_id=task.id,
            event_type="ASSIGNED",
            from_status="CREATED",
            to_status="ASSIGNED",
            reason="pk test",
        )
        db_session.add(audit)
        db_session.commit()

        for entity in (error, alias, location_alias, exception, evidence, task, audit):
            assert entity.id is not None, type(entity).__name__

        # ... and the rows are actually readable back from PostgreSQL.
        assert db_session.get(ImportBatchError, error.id) is not None
        assert db_session.get(Task, task.id) is not None
        assert db_session.get(ExceptionModel, exception.id) is not None


class TestSchemaParity:
    """The ORM must describe exactly the schema Alembic creates.

    The first PostgreSQL test run exposed two drifts that unit tests with a
    mocked session could never see: UUID primary keys declared as ``String(36)``
    and ``exceptions.po_line_number`` declared as a timestamp. A schema created
    from the models therefore rejected real inserts. This test compares every
    mapped column against the migrated database, so any future drift fails here.
    """

    def test_every_mapped_column_matches_the_migration(self, db_session):
        from app.db.base import Base

        inspector = inspect(db_session.get_bind())
        dialect = postgresql.dialect()
        mismatches: list[str] = []

        for table_name, table in Base.metadata.tables.items():
            db_columns = {c["name"]: c for c in inspector.get_columns(table_name)}

            if set(table.columns.keys()) != set(db_columns):
                mismatches.append(
                    f"{table_name}: ORM columns {sorted(table.columns.keys())} "
                    f"!= database columns {sorted(db_columns)}"
                )
                continue

            for column in table.columns:
                db_column = db_columns[column.name]
                orm_type = str(column.type.compile(dialect=dialect)).upper()
                db_type = str(db_column["type"].compile(dialect=dialect)).upper()
                if orm_type != db_type:
                    mismatches.append(f"{table_name}.{column.name}: type {orm_type} != {db_type}")
                if bool(column.nullable) != bool(db_column["nullable"]):
                    mismatches.append(
                        f"{table_name}.{column.name}: nullable "
                        f"{column.nullable} != {db_column['nullable']}"
                    )

            orm_unique = {
                tuple(sorted(c.name for c in constraint.columns))
                for constraint in table.constraints
                if constraint.__class__.__name__ == "UniqueConstraint"
            }
            db_unique = {
                tuple(sorted(constraint["column_names"]))
                for constraint in inspector.get_unique_constraints(table_name)
            }
            if orm_unique != db_unique:
                mismatches.append(
                    f"{table_name}: unique constraints {sorted(orm_unique)} != {sorted(db_unique)}"
                )

        assert mismatches == []

    def test_timestamp_defaults_are_callables(self, db_session):
        """Timestamp defaults must be evaluated per row, not at import time.

        ``default=datetime.datetime.now(datetime.UTC)`` calls ``now`` once when
        the model module is imported, freezing every row's timestamp to process
        start. The default must be the ``utc_now`` callable instead.
        """
        import time

        from sqlalchemy import DateTime

        from app.db.base import Base

        frozen: list[str] = []
        for table in Base.metadata.tables.values():
            for column in table.columns:
                if not isinstance(column.type, DateTime):
                    continue
                for kind, spec in (("default", column.default), ("onupdate", column.onupdate)):
                    if spec is not None and not callable(spec.arg):
                        frozen.append(f"{table.name}.{column.name} {kind}={spec.arg!r}")

        assert frozen == []

        from app.models.import_ import ImportBatch

        first = ImportBatch.__table__.c.started_at.default.arg(None)
        time.sleep(0.01)
        second = ImportBatch.__table__.c.started_at.default.arg(None)
        assert second > first

    def test_uuid_primary_keys_use_native_postgres_uuid(self, db_session):
        from app.db.base import Base

        for table_name, table in Base.metadata.tables.items():
            for column in table.primary_key.columns:
                if not isinstance(column.type, Uuid):
                    continue
                udt_name = db_session.execute(
                    text(
                        "SELECT udt_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = :table "
                        "AND column_name = :column"
                    ),
                    {"table": table_name, "column": column.name},
                ).scalar_one()
                assert udt_name == "uuid", f"{table_name}.{column.name} is {udt_name}"

    def test_po_line_number_is_an_integer(self, db_session):
        udt_name = db_session.execute(
            text(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'exceptions' "
                "AND column_name = 'po_line_number'"
            )
        ).scalar_one()
        assert udt_name == "int4"


class TestReconciliationReporting:
    """The reconciliation response must describe work actually performed."""

    def test_counts_match_the_first_and_repeat_run(self, db_session):
        import_all()

        first = run(run_reconciliation(evaluation_time=None, import_batch_id=None))
        # PO-501, PO-502, PO-505 and PO-506 are past deadline and OPEN;
        # PO-503 is future-dated and PO-504 is cancelled.
        assert first.total_po_lines_evaluated == 4
        assert first.exceptions_created == 3
        assert len(first.exceptions) == 3

        repeat = run(run_reconciliation(evaluation_time=None, import_batch_id=None))
        assert repeat.total_po_lines_evaluated == 4
        assert repeat.exceptions_created == 0, "a repeat run created no rows"
        assert len(repeat.exceptions) == 3

    def test_deadline_filtering_changes_the_evaluated_set(self, db_session):
        import_all()

        early = run(
            run_reconciliation(
                evaluation_time="2026-10-15T13:00:00+05:30",
                import_batch_id=None,
            )
        )
        # At 13:00 only PO-501 and PO-502 have passed their 12:00 deadline.
        assert early.total_po_lines_evaluated == 2
        assert early.exceptions_created == 1
        assert {e.business_key for e in early.exceptions} == {"SHORTFALL:PO-501:L1"}

        full = run(run_reconciliation(evaluation_time=None, import_batch_id=None))
        assert full.total_po_lines_evaluated == 4
        assert full.exceptions_created == 2
        assert {e.po_number for e in full.exceptions} == {"PO-501", "PO-505", "PO-506"}

    def test_batch_filter_applies_the_same_eligibility_rules(self, db_session):
        results = import_all()

        po_batch = results["purchase_order_lines"].import_id
        early = run(
            run_reconciliation(
                evaluation_time="2026-10-15T13:00:00+05:30",
                import_batch_id=po_batch,
            )
        )
        assert early.total_po_lines_evaluated == 2
        assert early.exceptions_created == 1

        receipt_batch = results["goods_receipt_lines"].import_id
        none = run(run_reconciliation(evaluation_time=None, import_batch_id=receipt_batch))
        assert none.total_po_lines_evaluated == 0
        assert none.exceptions_created == 0

    def test_receipt_after_evaluation_time_is_not_counted(self, db_session):
        import_all()

        late_receipt = (
            "source_record_id,receipt_number,receipt_line,po_number,po_line_number,"
            "location_code,product_ref,accepted_qty,uom,received_at,posting_status\n"
            "WMS-LATE-501-L1,GRN-LATE-501,1,PO-501,1,WH-MH-014,WMS-BRC-001,8,EA,"
            "2026-10-16T16:00:00+05:30,POSTED\n"
        )
        response = import_csv("goods_receipt_lines", late_receipt, "late-receipt")
        assert response.message == (
            "Import completed: 1 accepted, 0 rejected, 0 duplicates, 0 conflicts"
        )

        result = run(run_reconciliation(evaluation_time=None, import_batch_id=None))
        po501 = next(e for e in result.exceptions if e.po_number == "PO-501")
        # 60 + 32, not the 8 units received after the frozen evaluation time.
        assert float(po501.received_qty_base) == 92.0
        assert float(po501.variance_base) == 8.0

        source_ids = {e.source_record_id for e in po501.source_evidence}
        assert "WMS-GRN501-A-L1" in source_ids
        assert "WMS-LATE-501-L1" not in source_ids

    def test_receipt_dedupe_key_includes_the_source_system(self, db_session):
        """Record IDs are only unique inside their own source system.

        Deduplicating receipt lines on ``source_record_id`` alone silently
        discards a receipt from another system that happens to reuse an ID.
        The key must be ``(source_system, source_record_id)``.
        """
        from app.models.receipt_line import ReceiptLine

        results = import_all()
        receipt_batch_id = results["goods_receipt_lines"].import_id

        # Same record ID as the WMS line, but a different source system and a
        # different physical quantity.
        db_session.add(
            ReceiptLine(
                source_system="ERP",
                source_record_id="WMS-GRN501-A-L1",
                import_batch_id=uuid.UUID(receipt_batch_id),
                receipt_number="GRN-ERP-501",
                receipt_line=1,
                po_number="PO-501",
                po_line_number=1,
                canonical_product_id="SKU-001",
                canonical_location_id="STORE-014",
                accepted_qty_base=Decimal("1"),
                posting_status="POSTED",
                received_at=datetime.datetime.fromisoformat("2026-10-15T10:00:00+05:30"),
            )
        )
        db_session.commit()

        result = run(run_reconciliation(evaluation_time=None, import_batch_id=None))
        po501 = next(e for e in result.exceptions if e.po_number == "PO-501")
        assert float(po501.received_qty_base) == 93.0  # 60 + 32 + 1
        assert float(po501.variance_base) == 7.0

        source_systems = {e.source_system for e in po501.source_evidence}
        assert {"WMS", "ERP"} <= source_systems


class TestFailurePaths:
    """Documented failure paths must be persisted and reportable."""

    def test_conflicting_duplicate_is_flagged_without_overwrite(self, db_session):
        import_all()

        original = (FIXTURES / "purchase_order_lines.csv").read_text()
        conflicting = original.replace(
            "ERP-PO501-L1,PO-501,1,SUP-07,MUM-014,ERP-SKU-001,100,EA,OPEN",
            "ERP-PO501-L1,PO-501,1,SUP-07,MUM-014,ERP-SKU-001,101,EA,OPEN",
        )
        assert conflicting != original

        response = import_csv("purchase_order_lines", conflicting, "conflict")
        assert response.message == (
            "Import completed: 0 accepted, 0 rejected, 6 duplicates, 1 conflicts"
        )

        po501 = db_session.scalar(select(POLine).where(POLine.source_record_id == "ERP-PO501-L1"))
        assert po501 is not None
        assert po501.ordered_qty_base == Decimal("100")

        errors = db_session.scalars(
            select(ImportBatchError).where(ImportBatchError.error_code == "CONFLICTING_PO_LINE")
        ).all()
        assert len(errors) == 1
        assert errors[0].source_record_id == "ERP-PO501-L1"
        assert errors[0].row_number == 2

        quality = run(get_data_quality(limit=100))
        assert len(quality.conflicting_records) == 1
        assert quality.conflicting_records[0]["source_record_id"] == "ERP-PO501-L1"

    def test_unknown_product_mapping_is_a_data_quality_rejection(self, db_session):
        for dataset in ("product_aliases", "location_aliases"):
            response = run(
                import_dataset(
                    dataset=dataset,
                    file=upload(f"{dataset}.csv"),
                    import_reference="mapping-only",
                )
            )
            assert response.status == "COMPLETED"

        unknown = (
            "source_record_id,po_number,line_number,supplier_code,location_code,"
            "item_code,ordered_qty,ordered_uom,status,expected_receipt_by\n"
            "ERP-UNKNOWN-L1,PO-900,1,SUP-07,MUM-014,ERP-NOT-MAPPED,10,EA,OPEN,"
            "2026-10-15T12:00:00+05:30\n"
        )
        response = import_csv("purchase_order_lines", unknown, "unknown-product")
        assert response.message == (
            "Import completed: 0 accepted, 1 rejected, 0 duplicates, 0 conflicts"
        )

        errors = db_session.scalars(select(ImportBatchError)).all()
        assert len(errors) == 1
        assert errors[0].error_code == "UNKNOWN_PRODUCT"
        assert "ERP-NOT-MAPPED" in errors[0].error_message

        assert db_session.query(POLine).count() == 0
        assert db_session.query(ExceptionModel).count() == 0

        quality = run(get_data_quality(limit=100))
        assert [r.error_code for r in quality.rejected_records] == ["UNKNOWN_PRODUCT"]

    def test_task_without_eligible_assignee_stays_created_with_reason(self, db_session):
        import_all()

        # HR-401 is the only store manager at STORE-015 and there is no
        # receiving associate there, so taking HR-401 off shift leaves the
        # exception without an eligible assignee.
        updated = (
            db_session.query(WorkforceStatus)
            .filter(WorkforceStatus.source_employee_id == "HR-401")
            .update({"availability_status": "OFF_SHIFT"})
        )
        assert updated == 1
        db_session.commit()

        run(run_reconciliation(evaluation_time=None, import_batch_id=None))

        created = run(
            list_tasks(assignee_id=None, status="CREATED", exception_id=None, location_id=None)
        )
        assert len(created) == 1
        task = created[0]
        assert task.assignee_employee_id is None
        assert task.status == "CREATED"
        assert "STORE-015" in task.assignment_reason
        assert "No eligible receiving_associate" in task.assignment_reason

        detail = run(get_task_detail(task.id))
        assert detail.audit_events == []

        assigned = run(
            list_tasks(assignee_id=None, status="ASSIGNED", exception_id=None, location_id=None)
        )
        assert len(assigned) == 2
        assert {t.assignee_employee_id for t in assigned} == {"HR-102"}


class TestHTTPAPI:
    """Exercise the ASGI routes, not only the endpoint coroutine functions."""

    def test_imports_and_data_quality_over_http(self, db_session):
        imported: dict[str, str] = {}

        with TestClient(fastapi_app) as client:
            for dataset in DATASETS:
                path = FIXTURES / f"{dataset}.csv"
                response = client.post(
                    f"/api/v1/imports/{dataset}",
                    params={"import_reference": "http-test"},
                    files={"file": (path.name, path.read_bytes(), "text/csv")},
                )
                assert response.status_code == 200, (dataset, response.text)
                payload = response.json()
                assert payload["status"] == "COMPLETED", (dataset, payload)
                assert payload["dataset"] == dataset
                imported[dataset] = payload["import_id"]

            nested = client.get("/api/v1/imports/data-quality")
            assert nested.status_code == 200, nested.text
            assert nested.json()["conflicting_records"] == []
            assert nested.json()["rejected_records"] == []

            alias = client.get("/api/v1/data-quality")
            assert alias.status_code == 200, alias.text
            assert alias.json() == nested.json()

            batch = client.get(f"/api/v1/imports/{imported['product_aliases']}")
            assert batch.status_code == 200, batch.text
            assert batch.json()["dataset_name"] == "product_aliases"

            invalid = client.get("/api/v1/imports/not-a-uuid")
            assert invalid.status_code == 400, invalid.text

            missing = client.get(f"/api/v1/imports/{uuid.uuid4()}")
            assert missing.status_code == 404, missing.text

            bad_batch = client.post(
                "/api/v1/reconciliation/run",
                params={"import_batch_id": "not-a-uuid"},
            )
            assert bad_batch.status_code == 400, bad_batch.text


class TestWorkflowGuards:
    """Manual workflow actions must respect ownership and terminal states."""

    def _task_for_store014(self) -> str:
        import_all()
        run(run_reconciliation(evaluation_time=None, import_batch_id=None))
        tasks = run(
            list_tasks(assignee_id="HR-102", status=None, exception_id=None, location_id=None)
        )
        assert tasks
        return tasks[0].id

    def test_reassignment_requires_eligible_assignee_and_records_actor(self, db_session):
        task_id = self._task_for_store014()

        with pytest.raises(HTTPException) as inactive:
            run(
                reassign_task(
                    task_id,
                    ReassignRequest(assignee_employee_id="HR-103", reason="inactive"),
                )
            )
        assert inactive.value.status_code == 400
        assert "INACTIVE, not ACTIVE" in inactive.value.detail

        with pytest.raises(HTTPException) as off_shift:
            run(
                reassign_task(
                    task_id,
                    ReassignRequest(
                        assignee_employee_id="HR-101",
                        reason="off-shift receiver",
                        actor_employee_id="HR-201",
                    ),
                )
            )
        assert off_shift.value.status_code == 400
        assert "not eligible for assignment at STORE-014" in off_shift.value.detail

        with pytest.raises(HTTPException) as wrong_scope:
            run(
                reassign_task(
                    task_id,
                    ReassignRequest(
                        assignee_employee_id="HR-501",
                        reason="regional escalation",
                        actor_employee_id="HR-201",
                    ),
                )
            )
        assert wrong_scope.value.status_code == 400
        assert "not eligible for assignment at STORE-014" in wrong_scope.value.detail

        with pytest.raises(HTTPException) as bad_actor:
            run(
                reassign_task(
                    task_id,
                    ReassignRequest(
                        assignee_employee_id="HR-102",
                        reason="bad actor",
                        actor_employee_id="HR-999",
                    ),
                )
            )
        assert bad_actor.value.status_code == 400
        assert "HR-999" in bad_actor.value.detail

        # Nothing changed after the rejected attempts.
        assert run(get_task_detail(task_id)).assignee_employee_id == "HR-102"

        run(
            reassign_task(
                task_id,
                ReassignRequest(
                    assignee_employee_id="HR-201",
                    reason="manager cover",
                    actor_employee_id="HR-201",
                ),
            )
        )

        detail = run(get_task_detail(task_id))
        assert detail.assignee_employee_id == "HR-201"
        assert detail.assigned_by_employee_id == "HR-201"
        assert detail.assignment_reason == "manager cover"
        event = detail.audit_events[-1]
        assert event.event_type == "REASSIGNED"
        assert event.from_status == "HR-102"
        assert event.to_status == "HR-201"
        assert event.actor_employee_id == "HR-201"

    def test_reassignment_rejected_after_work_is_completed(self, db_session):
        task_id = self._task_for_store014()

        run(acknowledge_task(task_id))
        run(start_task(task_id))
        run(complete_task(task_id, CompleteRequest(resolution_code="SHORTFALL_CONFIRMED")))

        for expected_status in ("COMPLETED", "PENDING_VERIFICATION", "VERIFIED"):
            with pytest.raises(HTTPException) as rejected:
                run(
                    reassign_task(
                        task_id,
                        ReassignRequest(assignee_employee_id="HR-201", reason="late"),
                    )
                )
            assert rejected.value.status_code == 400
            assert expected_status in rejected.value.detail

            if expected_status == "COMPLETED":
                run(request_verification(task_id))
            elif expected_status == "PENDING_VERIFICATION":
                run(verify_task(task_id))

        assert run(get_task_detail(task_id)).status == "VERIFIED"
        assert run(get_task_detail(task_id)).assignee_employee_id == "HR-102"

    def test_completion_retry_after_verification_handoff_is_idempotent(self, db_session):
        task_id = self._task_for_store014()

        run(acknowledge_task(task_id))
        run(start_task(task_id))
        run(complete_task(task_id, CompleteRequest(resolution_code="SHORTFALL_CONFIRMED")))
        run(request_verification(task_id))

        retry = run(
            complete_task(
                task_id,
                CompleteRequest(resolution_code="SHORTFALL_CONFIRMED"),
            )
        )
        assert retry.status == "PENDING_VERIFICATION"
        assert retry.message == "Task was already completed (idempotent retry)"

        detail = run(get_task_detail(task_id))
        assert [e.event_type for e in detail.audit_events] == [
            "ASSIGNED",
            "ACKNOWLEDGED",
            "STARTED",
            "COMPLETED",
            "COMPLETION_RECORDED",
            "COMPLETION_RETRY",
        ]

        run(verify_task(task_id))

        with pytest.raises(HTTPException) as terminal:
            run(
                complete_task(
                    task_id,
                    CompleteRequest(resolution_code="SHORTFALL_CONFIRMED"),
                )
            )
        assert terminal.value.status_code == 400
        assert "VERIFIED is a terminal state" in terminal.value.detail
