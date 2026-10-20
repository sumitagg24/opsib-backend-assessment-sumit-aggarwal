"""Shortfall detector for receipt reconciliation."""

import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.exception import Exception as ExceptionModel
from app.models.exception_evidence import ExceptionEvidence
from app.models.po_line import POLine
from app.models.product import Product
from app.models.receipt_line import ReceiptLine


class ShortfallDetector:
    """Detects receipt shortfalls against purchase order lines."""

    def __init__(
        self,
        session: Session,
        rule_version: str = "1.0",
        mapping_version: str = "1.0",
        evaluation_time: datetime.datetime | None = None,
        tolerance: Decimal | None = None,
    ):
        """
        Initialize the detector.

        Args:
            session: Database session
            rule_version: Version of detection rules
            mapping_version: Version of mappings used
            evaluation_time: Time to use for deadline comparisons (defaults to frozen time)
            tolerance: Optional tolerance in base UOM units (default: 0)
        """
        self.session = session
        self.rule_version = rule_version
        self.mapping_version = mapping_version

        # Use provided evaluation time or frozen time from settings
        if evaluation_time:
            self.evaluation_time = evaluation_time
        else:
            from dateutil.parser import parse

            self.evaluation_time = parse(settings.frozen_evaluation_time)

        # Tolerance (default zero)
        self.tolerance = tolerance if tolerance is not None else Decimal("0")

        # Track work performed by the most recent run:
        # - evaluated_po_lines: eligible PO lines considered (not just findings)
        # - evaluated_exceptions: every shortfall, new or already persisted
        # - created_exceptions: only rows inserted by this run
        self.evaluated_po_lines = 0
        self.evaluated_exceptions: list[ExceptionModel] = []
        self.created_exceptions: list[ExceptionModel] = []

    def detect_shortfalls(self) -> list[ExceptionModel]:
        """
        Run shortfall detection on all eligible PO lines.

        Returns:
            List of created exceptions
        """
        po_lines = self._eligible_po_lines()
        self.evaluated_po_lines = len(po_lines)

        for po_line in po_lines:
            self._evaluate_po_line(po_line)

        return self.created_exceptions

    def _eligible_po_lines(self) -> list[POLine]:
        """PO lines that can produce a finding at the evaluation time.

        A line is eligible only when it is still OPEN, has a deadline, and that
        deadline has passed. CANCELLED/CLOSED lines and lines whose receiving
        window is still open are deliberately not evaluated.
        """
        return (
            self.session.query(POLine)
            .filter(
                and_(
                    POLine.status == "OPEN",
                    POLine.expected_receipt_by.isnot(None),
                    POLine.expected_receipt_by <= self.evaluation_time,
                )
            )
            .all()
        )

    def _evaluate_po_line(self, po_line: POLine):
        """
        Evaluate a single PO line for shortfall.

        Args:
            po_line: Purchase order line to evaluate
        """
        # Count each source record once (duplicate-safe aggregation) and ignore
        # receipts posted after the evaluation instant. ``received_at`` is when
        # the stock was received; a receipt dated after the frozen evaluation
        # time cannot have been known when the assessment was taken.
        candidates = (
            self.session.query(ReceiptLine)
            .filter(
                and_(
                    ReceiptLine.po_number == po_line.po_number,
                    ReceiptLine.po_line_number == po_line.line_number,
                    ReceiptLine.posting_status == "POSTED",
                    ReceiptLine.received_at <= self.evaluation_time,
                )
            )
            .all()
        )
        receipt_lines = []
        seen_source_keys: set[tuple[str, str]] = set()
        for receipt_line in candidates:
            source_key = (receipt_line.source_system, receipt_line.source_record_id)
            if source_key in seen_source_keys:
                continue
            seen_source_keys.add(source_key)
            receipt_lines.append(receipt_line)

        # Calculate total received quantity
        total_received = sum((rl.accepted_qty_base or Decimal("0")) for rl in receipt_lines)

        # Calculate variance
        expected = po_line.ordered_qty_base or Decimal("0")
        variance = expected - total_received

        # Check if shortfall exceeds tolerance
        if variance > self.tolerance:
            exception, created = self._create_exception(
                po_line=po_line,
                expected_qty=expected,
                received_qty=total_received,
                variance=variance,
                receipt_lines=receipt_lines,
            )
            self.evaluated_exceptions.append(exception)
            if created:
                self.created_exceptions.append(exception)

    def _create_exception(
        self,
        po_line: POLine,
        expected_qty: Decimal,
        received_qty: Decimal,
        variance: Decimal,
        receipt_lines: list[ReceiptLine],
    ) -> tuple[ExceptionModel, bool]:
        """
        Create (or refresh) an exception for a shortfall.

        Args:
            po_line: The PO line with shortfall
            expected_qty: Expected quantity in base UOM
            received_qty: Received quantity in base UOM
            variance: Variance (expected - received)
            receipt_lines: Receipt lines that were counted

        Returns:
            Tuple of (exception, created). ``created`` is False when an
            exception with the same business key already existed and was
            refreshed in place.
        """
        # Build business key (stable, deterministic)
        business_key = f"SHORTFALL:{po_line.po_number}:L{po_line.line_number}"

        # Create explanation
        expected_str = str(expected_qty)
        received_str = str(received_qty)
        variance_str = str(variance)
        base_uom = po_line.canonical_product_id  # Will get from product

        # Get product for UOM
        product = (
            self.session.query(Product)
            .filter(Product.canonical_product_id == po_line.canonical_product_id)
            .first()
        )
        base_uom = product.base_uom if product else "EA"

        # Build neutral explanation
        explanation = (
            f"Receipt shortfall detected for PO {po_line.po_number} line {po_line.line_number}. "
            f"Expected {expected_str} {base_uom}, received {received_str} {base_uom}. "
            f"Variance: {variance_str} {base_uom}. "
            f"Note: This finding indicates a discrepancy between ordered and received quantities. "
            f"The available evidence does not include supplier dispatch or ASN data, so "
            f"supplier fault cannot be determined from this finding alone. "
            f"Further investigation may be needed to determine root cause."
        )

        # Idempotency: a repeated run must reuse the exception for this business key
        # instead of inserting a second row. The database enforces uniqueness on
        # (exception_type, business_key), so a duplicate insert aborts the whole run.
        existing = (
            self.session.query(ExceptionModel)
            .filter(
                ExceptionModel.exception_type == "RECEIPT_SHORTFALL",
                ExceptionModel.business_key == business_key,
            )
            .first()
        )

        if existing:
            # Refresh the measured values in place; status/resolution history of a
            # previously opened exception is deliberately left untouched.
            existing.expected_qty_base = expected_qty
            existing.received_qty_base = received_qty
            existing.variance_base = variance
            existing.base_uom = base_uom
            existing.explanation = explanation
            self.session.flush()
            self._link_evidence(existing, po_line, receipt_lines)
            return existing, False

        # Create exception
        exception = ExceptionModel(
            exception_type="RECEIPT_SHORTFALL",
            business_key=business_key,
            status="OPEN",
            po_number=po_line.po_number,
            po_line_number=po_line.line_number,
            canonical_product_id=po_line.canonical_product_id,
            canonical_location_id=po_line.canonical_location_id,
            expected_qty_base=expected_qty,
            received_qty_base=received_qty,
            variance_base=variance,
            base_uom=base_uom,
            rule_version=self.rule_version,
            mapping_version=self.mapping_version,
            explanation=explanation,
        )

        self.session.add(exception)
        self.session.flush()  # populate exception.id so evidence rows can reference it

        self._link_evidence(exception, po_line, receipt_lines)

        return exception, True

    def _link_evidence(
        self,
        exception: ExceptionModel,
        po_line: POLine,
        receipt_lines: list[ReceiptLine],
    ) -> None:
        """Attach evidence rows that are not already linked to the exception.

        Evidence is unique per (exception, source system, source record), so a
        repeat run must not insert the same link twice.
        """
        existing_keys = {
            (e.source_system, e.source_record_id)
            for e in self.session.query(ExceptionEvidence)
            .filter(ExceptionEvidence.exception_id == exception.id)
            .all()
        }

        for rl in receipt_lines:
            key = (rl.source_system, rl.source_record_id)
            if key in existing_keys:
                continue
            self.session.add(
                ExceptionEvidence(
                    exception_id=exception.id,
                    source_system=rl.source_system,
                    source_record_id=rl.source_record_id,
                    record_type="RECEIPT_LINE",
                )
            )

        po_key = (po_line.source_system, po_line.source_record_id)
        if po_key not in existing_keys:
            self.session.add(
                ExceptionEvidence(
                    exception_id=exception.id,
                    source_system=po_line.source_system,
                    source_record_id=po_line.source_record_id,
                    record_type="PO_LINE",
                )
            )

        self.session.flush()

    def run_detection_on_fixtures(self, import_batch_id: UUID) -> list[ExceptionModel]:
        """
        Run detection on data from a specific import batch.

        Args:
            import_batch_id: ID of the import batch to process

        Returns:
            List of created exceptions
        """
        po_lines = (
            self.session.query(POLine)
            .filter(
                and_(
                    POLine.import_batch_id == import_batch_id,
                    POLine.status == "OPEN",
                    POLine.expected_receipt_by.isnot(None),
                    POLine.expected_receipt_by <= self.evaluation_time,
                )
            )
            .all()
        )
        self.evaluated_po_lines = len(po_lines)

        for po_line in po_lines:
            self._evaluate_po_line(po_line)

        return self.created_exceptions
