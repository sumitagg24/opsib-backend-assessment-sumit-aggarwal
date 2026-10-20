"""Workforce status ingestion handler."""

import csv
import io
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from dateutil.parser import parse as parse_datetime
from sqlalchemy.orm import Session

from app.ingestion.loader import CSVLoader
from app.models.employee import Employee
from app.models.import_ import ImportBatch, ImportError
from app.models.workforce_status import WorkforceStatus


class WorkforceStatusHandler:
    """Handles ingestion of workforce_status.csv."""

    EXPECTED_HEADERS = [
        "source_employee_id",
        "availability_status",
        "shift_start",
        "shift_end",
        "observed_at",
    ]

    VALID_AVAILABILITY_STATUSES = {"ON_SHIFT", "OFF_SHIFT"}

    def __init__(self, session: Session, import_batch: ImportBatch):
        self.session = session
        self.import_batch = import_batch
        self.loader = CSVLoader(session, import_batch)

    def process(self, file_content: bytes | str) -> dict[str, Any]:
        """Process workforce status CSV file."""
        accepted = 0
        rejected = 0
        duplicates = 0
        conflicts = 0

        try:
            rows = self._parse_csv(file_content)

            for row_number, row in enumerate(rows, start=2):
                result = self._process_row(row, row_number)
                # The session runs with autoflush=False, so flush each row before the
                # next row's lookup: otherwise pending inserts (status rows, duplicate
                # observations) are invisible to the duplicate checks.
                self.session.flush()

                if result["status"] == "accepted":
                    accepted += 1
                elif result["status"] == "rejected":
                    rejected += 1
                elif result["status"] == "duplicate":
                    duplicates += 1
                elif result["status"] == "conflict":
                    conflicts += 1

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
        availability_status = row.get("availability_status", "").strip().upper()
        shift_start_str = row.get("shift_start", "").strip()
        shift_end_str = row.get("shift_end", "").strip()
        observed_at_str = row.get("observed_at", "").strip()

        # Validate required fields
        if not source_employee_id:
            self._add_error(
                row, row_number, "MISSING_EMPLOYEE_ID", "source_employee_id is required"
            )
            return {"status": "rejected"}

        # Validate availability status
        if availability_status not in self.VALID_AVAILABILITY_STATUSES:
            self._add_error(
                row,
                row_number,
                "INVALID_AVAILABILITY_STATUS",
                f"Invalid availability_status: '{availability_status}'. "
                f"Must be one of: {', '.join(self.VALID_AVAILABILITY_STATUSES)}",
            )
            return {"status": "rejected"}

        # Parse and validate timestamps
        try:
            shift_start = parse_datetime(shift_start_str)
        except Exception:
            self._add_error(
                row,
                row_number,
                "INVALID_SHIFT_START",
                f"Cannot parse shift_start: '{shift_start_str}'",
            )
            return {"status": "rejected"}

        try:
            shift_end = parse_datetime(shift_end_str)
        except Exception:
            self._add_error(
                row, row_number, "INVALID_SHIFT_END", f"Cannot parse shift_end: '{shift_end_str}'"
            )
            return {"status": "rejected"}

        try:
            observed_at = parse_datetime(observed_at_str)
        except Exception:
            self._add_error(
                row,
                row_number,
                "INVALID_OBSERVED_AT",
                f"Cannot parse observed_at: '{observed_at_str}'",
            )
            return {"status": "rejected"}

        # Check that employee exists
        employee = (
            self.session.query(Employee)
            .filter(Employee.source_employee_id == source_employee_id)
            .first()
        )

        if not employee:
            self._add_error(
                row,
                row_number,
                "UNKNOWN_EMPLOYEE",
                f"Employee '{source_employee_id}' not found in employees",
            )
            return {"status": "rejected"}

        # Check for duplicate (same employee, same observed_at)
        existing = (
            self.session.query(WorkforceStatus)
            .filter(
                WorkforceStatus.source_employee_id == source_employee_id,
                WorkforceStatus.observed_at == observed_at,
            )
            .first()
        )

        if existing:
            if (
                existing.availability_status == availability_status
                and existing.shift_start == shift_start
                and existing.shift_end == shift_end
            ):
                return {
                    "status": "duplicate",
                    "source_record_id": f"{source_employee_id}:{observed_at.isoformat()}",
                }
            else:
                self._add_error(
                    row,
                    row_number,
                    "CONFLICTING_WORKFORCE_STATUS",
                    "Existing workforce status has different values for same observed_at",
                )
                return {"status": "conflict"}

        # Create workforce status
        status = WorkforceStatus(
            source_employee_id=source_employee_id,
            availability_status=availability_status,
            shift_start=shift_start,
            shift_end=shift_end,
            observed_at=observed_at,
            import_batch_id=self.import_batch.id,
        )
        self.session.add(status)

        return {"status": "accepted"}

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
