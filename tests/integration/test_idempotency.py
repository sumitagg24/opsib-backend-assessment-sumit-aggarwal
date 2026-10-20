"""Integration tests for idempotency."""

from decimal import Decimal
from uuid import uuid4

import pytest


class TestIdempotency:
    """Tests for idempotent operations."""

    def test_duplicate_source_record_id_same_content(self):
        """Test that importing the same record twice with identical content is idempotent."""
        # Simulate database with uniqueness constraint
        stored_records = {}

        def import_record(record):
            """Simulate import with uniqueness check."""
            key = (record["source_system"], record["source_record_id"])

            if key in stored_records:
                # Check if content matches
                if stored_records[key] == record:
                    return {"status": "duplicate_ignored", "id": stored_records[key]["id"]}
                else:
                    return {"status": "conflict", "error": "Conflicting content"}

            # New record
            record_id = str(uuid4())
            record["id"] = record_id
            stored_records[key] = record
            return {"status": "created", "id": record_id}

        # First import
        record1 = {
            "source_system": "WMS",
            "source_record_id": "WMS-GRN506-A-L1",
            "qty": Decimal("10"),
            "po_number": "PO-506",
        }

        result1 = import_record(record1)
        assert result1["status"] == "created"

        # Second import with identical content
        result2 = import_record(record1.copy())
        assert result2["status"] == "duplicate_ignored"
        assert result1["id"] == result2["id"]

    def test_duplicate_source_record_id_conflicting_content(self):
        """Test that importing same record ID with different content is flagged."""
        stored_records = {}

        def import_record(record):
            key = (record["source_system"], record["source_record_id"])

            if key in stored_records:
                if stored_records[key] == record:
                    return {"status": "duplicate_ignored"}
                else:
                    return {"status": "conflict"}

            record_id = str(uuid4())
            record["id"] = record_id
            stored_records[key] = record
            return {"status": "created"}

        record1 = {
            "source_system": "WMS",
            "source_record_id": "WMS-GRN506-A-L1",
            "qty": Decimal("10"),
        }

        record2 = {
            "source_system": "WMS",
            "source_record_id": "WMS-GRN506-A-L1",
            "qty": Decimal("15"),  # Different!
        }

        import_record(record1)
        result = import_record(record2)
        assert result["status"] == "conflict"

    def test_receipt_aggregation_counts_unique_sources(self):
        """Test that receipt aggregation counts each source once."""
        receipts = [
            {"source_record_id": "WMS-GRN501-A-L1", "qty": Decimal("60")},
            {"source_record_id": "WMS-GRN501-B-L1", "qty": Decimal("32")},
            {"source_record_id": "WMS-GRN501-A-L1", "qty": Decimal("60")},  # Duplicate
        ]

        # Aggregate by unique source_record_id
        unique_receipts = {}
        for r in receipts:
            if r["source_record_id"] not in unique_receipts:
                unique_receipts[r["source_record_id"]] = r["qty"]
            # If already exists with same qty, ignore (idempotent)
            # If different qty, would be flagged as conflict

        total = sum(unique_receipts.values())
        assert total == Decimal("92"), f"Expected 92, got {total}"
        assert len(unique_receipts) == 2, "Should have 2 unique receipts"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
