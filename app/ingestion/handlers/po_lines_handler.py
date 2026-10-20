"""Purchase order lines ingestion handler."""

import csv
import io
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from dateutil.parser import parse as parse_datetime
from sqlalchemy.orm import Session

from app.ingestion.loader import CSVLoader
from app.models.import_ import ImportBatch, ImportError
from app.models.location_alias import LocationAlias
from app.models.po_line import POLine
from app.models.product_alias import ProductAlias


class POLinesHandler:
    """Handles ingestion of purchase_order_lines.csv."""

    EXPECTED_HEADERS = [
        "source_record_id",
        "po_number",
        "line_number",
        "supplier_code",
        "location_code",
        "item_code",
        "ordered_qty",
        "ordered_uom",
        "status",
        "expected_receipt_by",
    ]

    VALID_STATUSES = {"OPEN", "CANCELLED", "CLOSED", "PARTIALLY_RECEIVED"}

    def __init__(self, session: Session, import_batch: ImportBatch):
        self.session = session
        self.import_batch = import_batch
        self.loader = CSVLoader(session, import_batch)

    def process(self, file_content: bytes | str) -> dict[str, Any]:
        """Process PO lines CSV file."""
        accepted = 0
        rejected = 0
        duplicates = 0
        conflicts = 0

        try:
            rows = self._parse_csv(file_content)

            for row_number, row in enumerate(rows, start=2):
                result = self._process_row(row, row_number)
                # The session runs with autoflush=False, so flush each row before the
                # next row's lookup: otherwise pending inserts (PO lines, duplicate
                # source records) are invisible to the duplicate checks.
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
        source_record_id = row.get("source_record_id", "").strip()
        po_number = row.get("po_number", "").strip()
        line_number_str = row.get("line_number", "").strip()
        supplier_code = row.get("supplier_code", "").strip()
        location_code = row.get("location_code", "").strip()
        item_code = row.get("item_code", "").strip()
        ordered_qty_str = row.get("ordered_qty", "").strip()
        status = row.get("status", "").strip().upper()
        expected_receipt_by_str = row.get("expected_receipt_by", "").strip()

        # Validate required fields
        if not source_record_id:
            self._add_error(
                row, row_number, "MISSING_SOURCE_RECORD_ID", "source_record_id is required"
            )
            return {"status": "rejected"}

        if not po_number:
            self._add_error(row, row_number, "MISSING_PO_NUMBER", "po_number is required")
            return {"status": "rejected"}

        # Parse line_number
        try:
            line_number = int(line_number_str)
        except (ValueError, TypeError):
            self._add_error(
                row,
                row_number,
                "INVALID_LINE_NUMBER",
                f"Cannot parse line_number: '{line_number_str}'",
            )
            return {"status": "rejected"}

        # Validate status
        if status not in self.VALID_STATUSES:
            self._add_error(
                row,
                row_number,
                "INVALID_STATUS",
                f"Invalid status: '{status}'. Must be one of: {', '.join(self.VALID_STATUSES)}",
            )
            return {"status": "rejected"}

        # Parse ordered quantity
        try:
            ordered_qty = Decimal(ordered_qty_str)
            if ordered_qty < 0:
                self._add_error(
                    row, row_number, "INVALID_ORDERED_QTY", "ordered_qty must be non-negative"
                )
                return {"status": "rejected"}
        except InvalidOperation:
            self._add_error(
                row,
                row_number,
                "INVALID_ORDERED_QTY",
                f"Cannot parse ordered_qty: '{ordered_qty_str}'",
            )
            return {"status": "rejected"}

        # Parse expected_receipt_by
        expected_receipt_by = None
        if expected_receipt_by_str:
            try:
                expected_receipt_by = parse_datetime(expected_receipt_by_str)
            except Exception:
                self._add_error(
                    row,
                    row_number,
                    "INVALID_EXPECTED_RECEIPT_BY",
                    f"Cannot parse expected_receipt_by: '{expected_receipt_by_str}'",
                )
                return {"status": "rejected"}

        # Map product
        product_alias = (
            self.session.query(ProductAlias)
            .filter(
                ProductAlias.source_system == "ERP",
                ProductAlias.external_item_id == item_code,
                ProductAlias.mapping_version == self.import_batch.mapping_version,
            )
            .first()
        )

        if not product_alias:
            self._add_error(
                row,
                row_number,
                "UNKNOWN_PRODUCT",
                f"Product '{item_code}' not found in product mappings",
            )
            return {"status": "rejected"}

        canonical_product_id = product_alias.canonical_product_id

        # Convert quantity to base UOM
        try:
            ordered_qty_base = ordered_qty * Decimal(str(product_alias.to_base_factor))
        except Exception as e:
            self._add_error(
                row, row_number, "UOM_CONVERSION_FAILED", f"Failed to convert quantity: {e}"
            )
            return {"status": "rejected"}

        # Map location
        location_alias = (
            self.session.query(LocationAlias)
            .filter(
                LocationAlias.source_system == "ERP",
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

        # Check for duplicate source_record_id
        existing = (
            self.session.query(POLine)
            .filter(
                POLine.source_system == "ERP",
                POLine.source_record_id == source_record_id,
            )
            .first()
        )

        if existing:
            # Check if identical
            if (
                existing.po_number == po_number
                and existing.line_number == line_number
                and existing.supplier_code == (supplier_code or None)
                and existing.canonical_product_id == canonical_product_id
                and existing.canonical_location_id == canonical_location_id
                and existing.ordered_qty_base == ordered_qty_base
                and existing.status == status
                and (
                    existing.expected_receipt_by is None
                    and expected_receipt_by is None
                    or existing.expected_receipt_by == expected_receipt_by
                )
            ):
                return {"status": "duplicate", "source_record_id": f"ERP:{source_record_id}"}
            else:
                self._add_error(
                    row,
                    row_number,
                    "CONFLICTING_PO_LINE",
                    "Existing PO line has different values for same source_record_id",
                )
                return {"status": "conflict"}

        # Create PO line
        po_line = POLine(
            source_system="ERP",
            source_record_id=source_record_id,
            import_batch_id=self.import_batch.id,
            po_number=po_number,
            line_number=line_number,
            supplier_code=supplier_code if supplier_code else None,
            canonical_product_id=canonical_product_id,
            canonical_location_id=canonical_location_id,
            ordered_qty_base=ordered_qty_base,
            status=status,
            expected_receipt_by=expected_receipt_by,
        )
        self.session.add(po_line)

        return {"status": "accepted"}

    def _add_error(self, row: dict[str, str], row_number: int, error_code: str, error_message: str):
        """Add an import error for the row."""
        error = ImportError(
            import_batch_id=self.import_batch.id,
            source_record_id=row.get("source_record_id", ""),
            row_number=row_number,
            error_code=error_code,
            error_message=error_message,
            raw_data=row,
        )
        self.session.add(error)
