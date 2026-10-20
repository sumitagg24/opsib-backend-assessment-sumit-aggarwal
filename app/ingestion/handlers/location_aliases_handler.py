"""Location aliases ingestion handler."""

import csv
import io
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.ingestion.loader import CSVLoader
from app.models.import_ import ImportBatch, ImportError
from app.models.location import Location
from app.models.location_alias import LocationAlias


class LocationAliasesHandler:
    """Handles ingestion of location_aliases.csv."""

    EXPECTED_HEADERS = [
        "source_system",
        "external_location_id",
        "canonical_location_id",
        "region_id",
    ]

    def __init__(self, session: Session, import_batch: ImportBatch):
        self.session = session
        self.import_batch = import_batch
        self.loader = CSVLoader(session, import_batch)

    def process(self, file_content: bytes | str) -> dict[str, Any]:
        """
        Process location aliases CSV file.

        Args:
            file_content: CSV content as bytes or string

        Returns:
            Dictionary with processing summary
        """
        accepted = 0
        rejected = 0
        duplicates = 0
        conflicts = 0

        try:
            rows = self._parse_csv(file_content)

            for row_number, row in enumerate(rows, start=2):
                result = self._process_row(row, row_number)
                # The session runs with autoflush=False, so flush each row before the
                # next row's lookup: otherwise pending inserts (canonical locations,
                # duplicate source records) are invisible to the duplicate checks.
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
        source_system = row.get("source_system", "").strip()
        external_location_id = row.get("external_location_id", "").strip()
        canonical_location_id = row.get("canonical_location_id", "").strip()
        region_id = row.get("region_id", "").strip()

        # Validate required fields
        if not source_system:
            self._add_error(row, row_number, "MISSING_SOURCE_SYSTEM", "source_system is required")
            return {"status": "rejected"}

        if not external_location_id:
            self._add_error(
                row, row_number, "MISSING_EXTERNAL_LOCATION_ID", "external_location_id is required"
            )
            return {"status": "rejected"}

        if not canonical_location_id:
            self._add_error(
                row,
                row_number,
                "MISSING_CANONICAL_LOCATION_ID",
                "canonical_location_id is required",
            )
            return {"status": "rejected"}

        if not region_id:
            self._add_error(row, row_number, "MISSING_REGION_ID", "region_id is required")
            return {"status": "rejected"}

        # Check for existing mapping
        existing = (
            self.session.query(LocationAlias)
            .filter(
                LocationAlias.source_system == source_system,
                LocationAlias.external_location_id == external_location_id,
                LocationAlias.mapping_version == self.import_batch.mapping_version,
            )
            .first()
        )

        if existing:
            if (
                existing.canonical_location_id == canonical_location_id
                and existing.region_id == region_id
            ):
                return {
                    "status": "duplicate",
                    "source_record_id": f"{source_system}:{external_location_id}",
                }
            else:
                self._add_error(
                    row,
                    row_number,
                    "CONFLICTING_MAPPING",
                    "Existing mapping has different values: "
                    f"canonical_location_id={existing.canonical_location_id}, "
                    f"region_id={existing.region_id}",
                )
                return {"status": "conflict"}

        # Create or get canonical location
        location = (
            self.session.query(Location)
            .filter(Location.canonical_location_id == canonical_location_id)
            .first()
        )

        if not location:
            location = Location(
                canonical_location_id=canonical_location_id,
                region_id=region_id,
                status="ACTIVE",
            )
            self.session.add(location)

        # Create location alias
        alias = LocationAlias(
            source_system=source_system,
            external_location_id=external_location_id,
            canonical_location_id=canonical_location_id,
            region_id=region_id,
            mapping_version=self.import_batch.mapping_version,
        )
        self.session.add(alias)

        return {"status": "accepted"}

    def _add_error(self, row: dict[str, str], row_number: int, error_code: str, error_message: str):
        """Add an import error for the row."""
        error = ImportError(
            import_batch_id=self.import_batch.id,
            source_record_id=(
                f"{row.get('source_system', '')}:{row.get('external_location_id', '')}"
            ),
            row_number=row_number,
            error_code=error_code,
            error_message=error_message,
            raw_data=row,
        )
        self.session.add(error)
