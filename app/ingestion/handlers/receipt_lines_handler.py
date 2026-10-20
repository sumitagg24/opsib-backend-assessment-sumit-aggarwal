"""Goods receipt lines ingestion handler."""

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
from app.models.product_alias import ProductAlias
from app.models.receipt_line import ReceiptLine


class ReceiptLinesHandler:
    """Handles ingestion of goods_receipt_lines.csv."""

    EXPECTED_HEADERS = [
        "source_record_id",
        "receipt_number",
        "receipt_line",
        "po_number",
        "po_line_number",
        "location_code",
        "product_ref",
        "accepted_qty",
        "uom",
        "received_at",
        "posting_status",
    ]

    VALID_POSTING_STATUSES = {"POSTED", "PENDING", "CANCELLED", "ADJUSTED"}

    def __init__(self, session: Session, import_batch: ImportBatch):
        self.session = session
        self.import_batch = import_batch
        self.loader = CSVLoader(session, import_batch)

    def process(self, file_content: bytes | str) -> dict[str, Any]:
        """Process receipt lines CSV file."""
        accepted = 0
        rejected = 0
        duplicates = 0
        conflicts = 0

        try:
            rows = self._parse_csv(file_content)

            for row_number, row in enumerate(rows, start=2):
                result = self._process_row(row, row_number)
                # The session runs with autoflush=False, so flush each row before the
                # next row's lookup: otherwise pending inserts (receipt lines,
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
        source_record_id = row.get("source_record_id", "").strip()
        receipt_number = row.get("receipt_number", "").strip()
        receipt_line_str = row.get("receipt_line", "").strip()
        po_number = row.get("po_number", "").strip()
        po_line_number_str = row.get("po_line_number", "").strip()
        location_code = row.get("location_code", "").strip()
        product_ref = row.get("product_ref", "").strip()
        accepted_qty_str = row.get("accepted_qty", "").strip()
        received_at_str = row.get("received_at", "").strip()
        posting_status = row.get("posting_status", "").strip().upper()

        # Validate required fields
        if not source_record_id:
            self._add_error(
                row, row_number, "MISSING_SOURCE_RECORD_ID", "source_record_id is required"
            )
            return {"status": "rejected"}

        if not receipt_number:
            self._add_error(row, row_number, "MISSING_RECEIPT_NUMBER", "receipt_number is required")
            return {"status": "rejected"}

        # Parse receipt_line
        try:
            receipt_line = int(receipt_line_str)
        except (ValueError, TypeError):
            self._add_error(
                row,
                row_number,
                "INVALID_RECEIPT_LINE",
                f"Cannot parse receipt_line: '{receipt_line_str}'",
            )
            return {"status": "rejected"}

        # Parse po_line_number
        try:
            po_line_number = int(po_line_number_str)
        except (ValueError, TypeError):
            self._add_error(
                row,
                row_number,
                "INVALID_PO_LINE_NUMBER",
                f"Cannot parse po_line_number: '{po_line_number_str}'",
            )
            return {"status": "rejected"}

        # Validate posting_status
        if posting_status not in self.VALID_POSTING_STATUSES:
            self._add_error(
                row,
                row_number,
                "INVALID_POSTING_STATUS",
                f"Invalid posting_status: '{posting_status}'. "
                f"Must be one of: {', '.join(self.VALID_POSTING_STATUSES)}",
            )
            return {"status": "rejected"}

        # Parse accepted_qty
        try:
            accepted_qty = Decimal(accepted_qty_str)
            if accepted_qty < 0:
                self._add_error(
                    row, row_number, "INVALID_ACCEPTED_QTY", "accepted_qty must be non-negative"
                )
                return {"status": "rejected"}
        except InvalidOperation:
            self._add_error(
                row,
                row_number,
                "INVALID_ACCEPTED_QTY",
                f"Cannot parse accepted_qty: '{accepted_qty_str}'",
            )
            return {"status": "rejected"}

        # Parse received_at
        try:
            received_at = parse_datetime(received_at_str)
        except Exception:
            self._add_error(
                row,
                row_number,
                "INVALID_RECEIVED_AT",
                f"Cannot parse received_at: '{received_at_str}'",
            )
            return {"status": "rejected"}

        # Map product
        product_alias = (
            self.session.query(ProductAlias)
            .filter(
                ProductAlias.source_system == "WMS",
                ProductAlias.external_item_id == product_ref,
                ProductAlias.mapping_version == self.import_batch.mapping_version,
            )
            .first()
        )

        if not product_alias:
            self._add_error(
                row,
                row_number,
                "UNKNOWN_PRODUCT",
                f"Product '{product_ref}' not found in product mappings",
            )
            return {"status": "rejected"}

        canonical_product_id = product_alias.canonical_product_id

        # Convert quantity to base UOM
        try:
            accepted_qty_base = accepted_qty * Decimal(str(product_alias.to_base_factor))
        except Exception as e:
            self._add_error(
                row, row_number, "UOM_CONVERSION_FAILED", f"Failed to convert quantity: {e}"
            )
            return {"status": "rejected"}

        # Map location
        location_alias = (
            self.session.query(LocationAlias)
            .filter(
                LocationAlias.source_system == "WMS",
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
            self.session.query(ReceiptLine)
            .filter(
                ReceiptLine.source_system == "WMS",
                ReceiptLine.source_record_id == source_record_id,
            )
            .first()
        )

        if existing:
            # Check if identical
            if (
                existing.receipt_number == receipt_number
                and existing.receipt_line == receipt_line
                and existing.po_number == po_number
                and existing.po_line_number == po_line_number
                and existing.canonical_product_id == canonical_product_id
                and existing.canonical_location_id == canonical_location_id
                and existing.accepted_qty_base == accepted_qty_base
                and existing.posting_status == posting_status
                and existing.received_at == received_at
            ):
                return {"status": "duplicate", "source_record_id": f"WMS:{source_record_id}"}
            else:
                self._add_error(
                    row,
                    row_number,
                    "CONFLICTING_RECEIPT_LINE",
                    "Existing receipt line has different values for same source_record_id",
                )
                return {"status": "conflict"}

        # Create receipt line
        receipt_line_obj = ReceiptLine(
            source_system="WMS",
            source_record_id=source_record_id,
            import_batch_id=self.import_batch.id,
            receipt_number=receipt_number,
            receipt_line=receipt_line,
            po_number=po_number,
            po_line_number=po_line_number,
            canonical_product_id=canonical_product_id,
            canonical_location_id=canonical_location_id,
            accepted_qty_base=accepted_qty_base,
            posting_status=posting_status,
            received_at=received_at,
        )
        self.session.add(receipt_line_obj)

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
