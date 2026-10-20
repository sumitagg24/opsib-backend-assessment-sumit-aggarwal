"""Employees ingestion handler."""

import csv
import io
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.ingestion.loader import CSVLoader
from app.models.employee import Employee
from app.models.import_ import ImportBatch, ImportError
from app.models.location_alias import LocationAlias


class EmployeesHandler:
    """Handles ingestion of employees.csv."""

    EXPECTED_HEADERS = [
        "source_employee_id",
        "display_name",
        "employment_status",
        "role",
        "location_code",
        "manager_employee_id",
        "region_id",
    ]

    VALID_EMPLOYMENT_STATUSES = {"ACTIVE", "INACTIVE"}
    VALID_ROLES = {"receiving_associate", "store_manager", "regional_operations"}

    def __init__(self, session: Session, import_batch: ImportBatch):
        self.session = session
        self.import_batch = import_batch
        self.loader = CSVLoader(session, import_batch)
        # (employee, manager id, row, row number) pairs whose manager row has not
        # been inserted yet; resolved in _resolve_pending_managers().
        self._pending_managers: list[tuple[Employee, str, dict[str, str], int]] = []

    def process(self, file_content: bytes | str) -> dict[str, Any]:
        """Process employees CSV file."""
        accepted = 0
        rejected = 0
        duplicates = 0
        conflicts = 0

        try:
            rows = self._parse_csv(file_content)

            for row_number, row in enumerate(rows, start=2):
                result = self._process_row(row, row_number)
                # The session runs with autoflush=False, so flush each row before the
                # next row's lookup: otherwise pending inserts (employees, row errors)
                # are invisible to the duplicate checks.
                self.session.flush()

                if result["status"] == "accepted":
                    accepted += 1
                elif result["status"] == "rejected":
                    rejected += 1
                elif result["status"] == "duplicate":
                    duplicates += 1
                elif result["status"] == "conflict":
                    conflicts += 1

            # Apply manager links deferred during the row pass; they can only be
            # written once every employee row has been inserted.
            self._resolve_pending_managers()

            self.import_batch.total_rows = accepted + rejected + duplicates + conflicts
            self.import_batch.accepted_rows = accepted
            self.import_batch.rejected_rows = rejected
            self.import_batch.duplicate_rows = duplicates
            self.import_batch.conflicting_rows = conflicts
            self.import_batch.status = "COMPLETED"
            self.import_batch.completed_at = datetime.now(UTC)

            self.session.add(self.import_batch)
            self.session.commit()

            return {
                "accepted": accepted,
                "rejected": rejected,
                "duplicates": duplicates,
                "conflicts": conflicts,
                "status": "COMPLETED",
            }

        except Exception as e:
            # Roll back the failed flush first; without it this FAILED update can
            # never commit and the batch would stay stuck in PROCESSING forever.
            self.session.rollback()
            self.import_batch.status = "FAILED"
            self.import_batch.error_message = str(e)
            self.session.add(self.import_batch)
            self.session.commit()
            raise

    def _parse_csv(self, file_content: bytes | str) -> Generator[dict[str, str], None, None]:
        """Parse CSV and yield rows."""
        text = file_content.decode("utf-8") if isinstance(file_content, bytes) else file_content

        reader = csv.DictReader(io.StringIO(text))

        if not reader.fieldnames:
            raise ValueError("CSV has no headers")

        missing_headers = set(self.EXPECTED_HEADERS) - set(reader.fieldnames)
        if missing_headers:
            raise ValueError(f"Missing required headers: {', '.join(missing_headers)}")

        yield from reader

    def _process_row(self, row: dict[str, str], row_number: int) -> dict[str, Any]:
        """Process a single row."""
        source_employee_id = row.get("source_employee_id", "").strip()
        display_name = row.get("display_name", "").strip()
        employment_status = row.get("employment_status", "").strip().upper()
        role = row.get("role", "").strip().lower()
        location_code = row.get("location_code", "").strip()
        manager_employee_id = row.get("manager_employee_id", "").strip()
        region_id = row.get("region_id", "").strip()

        # Validate required fields
        if not source_employee_id:
            self._add_error(
                row, row_number, "MISSING_EMPLOYEE_ID", "source_employee_id is required"
            )
            return {"status": "rejected"}

        if not display_name:
            self._add_error(row, row_number, "MISSING_DISPLAY_NAME", "display_name is required")
            return {"status": "rejected"}

        # Validate employment status
        if employment_status not in self.VALID_EMPLOYMENT_STATUSES:
            self._add_error(
                row,
                row_number,
                "INVALID_EMPLOYMENT_STATUS",
                f"Invalid employment_status: '{employment_status}'. "
                f"Must be one of: {', '.join(self.VALID_EMPLOYMENT_STATUSES)}",
            )
            return {"status": "rejected"}

        # Validate role
        if role not in self.VALID_ROLES:
            self._add_error(
                row,
                row_number,
                "INVALID_ROLE",
                f"Invalid role: '{role}'. Must be one of: {', '.join(self.VALID_ROLES)}",
            )
            return {"status": "rejected"}

        # Check for existing employee
        existing = (
            self.session.query(Employee)
            .filter(Employee.source_employee_id == source_employee_id)
            .first()
        )

        if existing:
            # Check if identical
            if (
                existing.display_name == display_name
                and existing.employment_status == employment_status
                and existing.role == role
                and (existing.region_id or "") == region_id
            ):
                return {"status": "duplicate", "source_record_id": source_employee_id}
            else:
                self._add_error(
                    row,
                    row_number,
                    "CONFLICTING_EMPLOYEE",
                    "Existing employee has different values",
                )
                return {"status": "conflict"}

        # Map location if provided
        canonical_location_id = None
        if location_code:
            location_alias = (
                self.session.query(LocationAlias)
                .filter(
                    LocationAlias.source_system == "HRMS",
                    LocationAlias.external_location_id == location_code,
                    LocationAlias.mapping_version == self.import_batch.mapping_version,
                )
                .first()
            )

            if not location_alias:
                self._add_error(
                    row,
                    row_number,
                    "UNKNOWN_LOCATION",
                    f"Location '{location_code}' not found in location mappings",
                )
                return {"status": "rejected"}

            canonical_location_id = location_alias.canonical_location_id

        # Create employee.
        # A report may appear before its manager row (HR-101 -> HR-201) and the
        # employees.manager_employee_id foreign key is not deferrable, so the link
        # is deferred until every employee row has been inserted.
        resolved_manager_id = None
        deferred_manager_id = None
        if manager_employee_id:
            manager_exists = (
                self.session.query(Employee)
                .filter(Employee.source_employee_id == manager_employee_id)
                .first()
            )
            if manager_exists:
                resolved_manager_id = manager_employee_id
            else:
                deferred_manager_id = manager_employee_id
        employee = Employee(
            source_employee_id=source_employee_id,
            display_name=display_name,
            employment_status=employment_status,
            role=role,
            canonical_location_id=canonical_location_id,
            manager_employee_id=resolved_manager_id,
            region_id=region_id if region_id else None,
        )
        self.session.add(employee)

        if deferred_manager_id:
            self._pending_managers.append((employee, deferred_manager_id, row, row_number))

        return {"status": "accepted"}

    def _resolve_pending_managers(self):
        """Assign manager links that were deferred during the row pass.

        The manager foreign key is checked at INSERT time, so a report row whose
        manager has not been inserted yet is held back here. Managers that exist
        neither in this file nor in the database are reported as row-level errors
        instead of failing the whole import.
        """
        for employee, manager_id, row, row_number in self._pending_managers:
            manager = (
                self.session.query(Employee)
                .filter(Employee.source_employee_id == manager_id)
                .first()
            )
            if manager:
                employee.manager_employee_id = manager.source_employee_id
            else:
                employee.manager_employee_id = None
                self._add_error(
                    row,
                    row_number,
                    "UNKNOWN_MANAGER",
                    f"Manager '{manager_id}' not found in employees",
                )
        self._pending_managers.clear()
        self.session.flush()

    def _add_error(self, row: dict[str, str], row_number: int, error_code: str, error_message: str):
        """Add an import error for the row."""
        error = ImportError(
            import_batch_id=self.import_batch.id,
            source_record_id=row.get("source_employee_id", ""),
            row_number=row_number,
            error_code=error_code,
            error_message=error_message,
            raw_data=row,
        )
        self.session.add(error)
