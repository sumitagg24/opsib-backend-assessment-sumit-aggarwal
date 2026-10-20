"""Generic CSV loader."""

import csv
import io
from collections.abc import Callable, Generator
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.models.import_ import ImportBatch, ImportError


class CSVLoader:
    """Loads CSV data and validates rows."""

    def __init__(
        self,
        session: Session,
        import_batch: ImportBatch,
        validator: Callable[[dict[str, str], int], tuple[bool, str | None]] | None = None,
    ):
        """
        Initialize the CSV loader.

        Args:
            session: Database session
            import_batch: Import batch to track progress
            validator: Optional row validator function
        """
        self.session = session
        self.import_batch = import_batch
        self.validator = validator
        self.row_errors = []

    def load_csv(
        self,
        file_content: bytes | str,
        expected_columns: list[str] | None = None,
        delimiter: str = ",",
    ) -> Generator[dict[str, str], None, None]:
        """
        Load CSV content and yield rows.

        Args:
            file_content: CSV content as bytes or string
            expected_columns: Optional list of expected column names
            delimiter: CSV delimiter (default: comma)

        Yields:
            Dictionary of column_name -> value for each row
        """
        text = file_content.decode("utf-8") if isinstance(file_content, bytes) else file_content

        # Parse CSV
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

        if not reader.fieldnames:
            self._add_error(
                source_record_id=None,
                row_number=0,
                error_code="CSV_PARSE_ERROR",
                error_message="CSV has no headers",
                raw_data={"content": text[:500]},
            )
            return

        # Validate columns if expected columns provided
        if expected_columns:
            missing = set(expected_columns) - set(reader.fieldnames)
            if missing:
                self._add_error(
                    source_record_id=None,
                    row_number=0,
                    error_code="CSV_COLUMN_MISMATCH",
                    error_message=f"Missing columns: {', '.join(missing)}",
                    raw_data={"expected": expected_columns, "actual": list(reader.fieldnames)},
                )
                return

        # Yield rows
        for _row_number, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
            yield row

    def validate_and_process(
        self,
        row: dict[str, str],
        row_number: int,
        processor: Callable[[dict[str, str], int], tuple[bool, dict[str, Any] | None, str | None]],
    ) -> tuple[bool, dict[str, Any] | None, str | None]:
        """
        Validate and process a row.

        Args:
            row: Row data as dictionary
            row_number: Row number in CSV
            processor: Function to process the row

        Returns:
            Tuple of (success, processed_data, error_message)
        """
        # Run custom validator if provided
        if self.validator:
            is_valid, validation_error = self.validator(row, row_number)
            if not is_valid:
                self._add_error(
                    source_record_id=row.get("source_record_id"),
                    row_number=row_number,
                    error_code="VALIDATION_ERROR",
                    error_message=validation_error,
                    raw_data=row,
                )
                return (False, None, validation_error)

        # Process the row
        success, processed_data, error = processor(row, row_number)

        if not success:
            self._add_error(
                source_record_id=row.get("source_record_id"),
                row_number=row_number,
                error_code="PROCESSING_ERROR",
                error_message=error or "Unknown processing error",
                raw_data=row,
            )
            return (False, None, error)

        return (True, processed_data, None)

    def _add_error(
        self,
        source_record_id: str | None,
        row_number: int | None,
        error_code: str,
        error_message: str,
        raw_data: dict[str, Any] | None = None,
    ):
        """Add an import error."""
        error = ImportError(
            import_batch_id=self.import_batch.id,
            source_record_id=source_record_id,
            row_number=row_number,
            error_code=error_code,
            error_message=error_message,
            raw_data=raw_data,
        )
        self.session.add(error)
        self.row_errors.append(error)

    @staticmethod
    def parse_decimal(value: str, field_name: str = "value") -> tuple[Decimal | None, str | None]:
        """
        Parse a decimal value from string.

        Args:
            value: String value to parse
            field_name: Name of the field for error messages

        Returns:
            Tuple of (parsed_value, error_message)
        """
        if not value or not value.strip():
            return (None, f"{field_name} is empty")

        try:
            return (Decimal(value.strip()), None)
        except InvalidOperation:
            return (None, f"Invalid decimal value for {field_name}: {value}")

    @staticmethod
    def parse_datetime(value: str, field_name: str = "datetime") -> tuple[str | None, str | None]:
        """
        Parse and validate a datetime string.

        Args:
            value: Datetime string
            field_name: Name of the field for error messages

        Returns:
            Tuple of (parsed_datetime_string, error_message)
            Returns ISO format string if valid, None if invalid
        """
        if not value or not value.strip():
            return (None, f"{field_name} is empty")

        # Try to parse the datetime
        from dateutil.parser import parse as parse_dt

        try:
            dt = parse_dt(value.strip())
            # Return ISO format string
            return (dt.isoformat(), None)
        except Exception:
            return (None, f"Invalid datetime format for {field_name}: {value}")
