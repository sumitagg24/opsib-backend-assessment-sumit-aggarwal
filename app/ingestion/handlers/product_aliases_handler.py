"""Product aliases ingestion handler."""

import csv
import io
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.ingestion.loader import CSVLoader
from app.models.import_ import ImportBatch, ImportError
from app.models.product import Product
from app.models.product_alias import ProductAlias


class ProductAliasesHandler:
    """Handles ingestion of product_aliases.csv."""

    EXPECTED_HEADERS = [
        "source_system",
        "external_item_id",
        "canonical_product_id",
        "source_uom",
        "base_uom",
        "to_base_factor",
    ]

    def __init__(self, session: Session, import_batch: ImportBatch):
        self.session = session
        self.import_batch = import_batch
        self.loader = CSVLoader(session, import_batch)

    def process(self, file_content: bytes | str) -> dict[str, Any]:
        """
        Process product aliases CSV file.

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
            # Parse and validate CSV
            rows = self._parse_csv(file_content)

            for row_number, row in enumerate(rows, start=2):  # Start at 2 (header is row 1)
                result = self._process_row(row, row_number)
                # The session runs with autoflush=False, so flush each row before the
                # next row's lookup: otherwise pending inserts (canonical products,
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

            # Update import batch
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

        # Validate headers
        if not reader.fieldnames:
            raise ValueError("CSV has no headers")

        missing_headers = set(self.EXPECTED_HEADERS) - set(reader.fieldnames)
        if missing_headers:
            raise ValueError(f"Missing required headers: {', '.join(missing_headers)}")

        yield from reader

    def _process_row(self, row: dict[str, str], row_number: int) -> dict[str, Any]:
        """
        Process a single row.

        Returns:
            Dictionary with status and optional error
        """
        source_system = row.get("source_system", "").strip()
        external_item_id = row.get("external_item_id", "").strip()
        canonical_product_id = row.get("canonical_product_id", "").strip()
        source_uom = row.get("source_uom", "").strip()
        base_uom = row.get("base_uom", "").strip()

        # Validate required fields
        if not source_system:
            self._add_error(row, row_number, "MISSING_SOURCE_SYSTEM", "source_system is required")
            return {"status": "rejected"}

        if not external_item_id:
            self._add_error(
                row, row_number, "MISSING_EXTERNAL_ITEM_ID", "external_item_id is required"
            )
            return {"status": "rejected"}

        if not canonical_product_id:
            self._add_error(
                row, row_number, "MISSING_CANONICAL_PRODUCT_ID", "canonical_product_id is required"
            )
            return {"status": "rejected"}

        # Parse and validate to_base_factor
        to_base_factor_str = row.get("to_base_factor", "").strip()
        if not to_base_factor_str:
            self._add_error(row, row_number, "MISSING_TO_BASE_FACTOR", "to_base_factor is required")
            return {"status": "rejected"}

        try:
            to_base_factor = Decimal(to_base_factor_str)
            if to_base_factor <= 0:
                self._add_error(
                    row, row_number, "INVALID_TO_BASE_FACTOR", "to_base_factor must be positive"
                )
                return {"status": "rejected"}
        except InvalidOperation:
            self._add_error(
                row,
                row_number,
                "INVALID_TO_BASE_FACTOR",
                f"Cannot parse '{to_base_factor_str}' as decimal",
            )
            return {"status": "rejected"}

        # Check for existing mapping (idempotency and conflict detection)
        existing = (
            self.session.query(ProductAlias)
            .filter(
                ProductAlias.source_system == source_system,
                ProductAlias.external_item_id == external_item_id,
                ProductAlias.mapping_version == self.import_batch.mapping_version,
            )
            .first()
        )

        if existing:
            # Check if identical (idempotent) or conflicting
            if (
                existing.source_uom == source_uom
                and existing.base_uom == base_uom
                and existing.to_base_factor == to_base_factor
                and existing.canonical_product_id == canonical_product_id
            ):
                # Identical - idempotent duplicate
                return {
                    "status": "duplicate",
                    "source_record_id": f"{source_system}:{external_item_id}",
                }
            else:
                # Conflicting - different content for same source ID
                self._add_error(
                    row,
                    row_number,
                    "CONFLICTING_MAPPING",
                    "Existing mapping has different values: "
                    f"source_uom={existing.source_uom}, "
                    f"base_uom={existing.base_uom}, "
                    f"to_base_factor={existing.to_base_factor}",
                )
                return {"status": "conflict"}

        # Create or get canonical product
        product = (
            self.session.query(Product)
            .filter(Product.canonical_product_id == canonical_product_id)
            .first()
        )

        if not product:
            product = Product(
                canonical_product_id=canonical_product_id,
                base_uom=base_uom,
                status="ACTIVE",
            )
            self.session.add(product)

        # Create product alias
        alias = ProductAlias(
            source_system=source_system,
            external_item_id=external_item_id,
            canonical_product_id=canonical_product_id,
            source_uom=source_uom,
            base_uom=base_uom,
            to_base_factor=to_base_factor,
            mapping_version=self.import_batch.mapping_version,
        )
        self.session.add(alias)

        return {"status": "accepted"}

    def _add_error(self, row: dict[str, str], row_number: int, error_code: str, error_message: str):
        """Add an import error for the row."""
        error = ImportError(
            import_batch_id=self.import_batch.id,
            source_record_id=f"{row.get('source_system', '')}:{row.get('external_item_id', '')}",
            row_number=row_number,
            error_code=error_code,
            error_message=error_message,
            raw_data=row,
        )
        self.session.add(error)
