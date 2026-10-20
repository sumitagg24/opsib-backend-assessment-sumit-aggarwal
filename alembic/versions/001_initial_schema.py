"""Initial schema for OPSiB backend

Revision ID: 001
Revises:
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enable UUID extension
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # Canonical Products
    op.create_table(
        "products",
        sa.Column("canonical_product_id", sa.String(50), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("base_uom", sa.String(20), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Canonical Locations
    op.create_table(
        "locations",
        sa.Column("canonical_location_id", sa.String(50), primary_key=True),
        sa.Column("region_id", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Product Aliases (source → canonical mapping)
    op.create_table(
        "product_aliases",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_system", sa.String(50), nullable=False),
        sa.Column("external_item_id", sa.String(100), nullable=False),
        sa.Column(
            "canonical_product_id",
            sa.String(50),
            sa.ForeignKey("products.canonical_product_id"),
            nullable=False,
        ),
        sa.Column("source_uom", sa.String(20), nullable=False),
        sa.Column("base_uom", sa.String(20), nullable=False),
        sa.Column("to_base_factor", sa.Numeric(15, 4), nullable=False),
        sa.Column("mapping_version", sa.String(50), nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("source_system", "external_item_id", "mapping_version"),
    )

    # Location Aliases (source → canonical mapping)
    op.create_table(
        "location_aliases",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_system", sa.String(50), nullable=False),
        sa.Column("external_location_id", sa.String(100), nullable=False),
        sa.Column(
            "canonical_location_id",
            sa.String(50),
            sa.ForeignKey("locations.canonical_location_id"),
            nullable=False,
        ),
        sa.Column("region_id", sa.String(50), nullable=False),
        sa.Column("mapping_version", sa.String(50), nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("source_system", "external_location_id", "mapping_version"),
    )

    # Import Batches
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("dataset_name", sa.String(100), nullable=False),
        sa.Column("import_reference", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="PROCESSING"),
        sa.Column("total_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("accepted_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rejected_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duplicate_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("conflicting_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rule_version", sa.String(50), nullable=False),
        sa.Column("mapping_version", sa.String(50), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )

    # Import Errors (row-level)
    op.create_table(
        "import_errors",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("import_batch_id", sa.Uuid, sa.ForeignKey("import_batches.id"), nullable=False),
        sa.Column("source_record_id", sa.String(100), nullable=True),
        sa.Column("row_number", sa.Integer, nullable=True),
        sa.Column("error_code", sa.String(100), nullable=False),
        sa.Column("error_message", sa.Text, nullable=False),
        sa.Column("raw_data", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Purchase Order Lines
    op.create_table(
        "po_lines",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_system", sa.String(50), nullable=False),
        sa.Column("source_record_id", sa.String(100), nullable=False),
        sa.Column("import_batch_id", sa.Uuid, sa.ForeignKey("import_batches.id"), nullable=False),
        sa.Column("po_number", sa.String(50), nullable=False),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("supplier_code", sa.String(50), nullable=True),
        sa.Column(
            "canonical_product_id",
            sa.String(50),
            sa.ForeignKey("products.canonical_product_id"),
            nullable=False,
        ),
        sa.Column(
            "canonical_location_id",
            sa.String(50),
            sa.ForeignKey("locations.canonical_location_id"),
            nullable=False,
        ),
        sa.Column("ordered_qty_base", sa.Numeric(15, 4), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("expected_receipt_by", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("source_system", "source_record_id"),
        sa.UniqueConstraint("import_batch_id", "po_number", "line_number"),
    )

    # Goods Receipt Lines
    op.create_table(
        "receipt_lines",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_system", sa.String(50), nullable=False),
        sa.Column("source_record_id", sa.String(100), nullable=False),
        sa.Column("import_batch_id", sa.Uuid, sa.ForeignKey("import_batches.id"), nullable=False),
        sa.Column("receipt_number", sa.String(50), nullable=False),
        sa.Column("receipt_line", sa.Integer, nullable=False),
        sa.Column("po_number", sa.String(50), nullable=False),
        sa.Column("po_line_number", sa.Integer, nullable=False),
        sa.Column(
            "canonical_product_id",
            sa.String(50),
            sa.ForeignKey("products.canonical_product_id"),
            nullable=False,
        ),
        sa.Column(
            "canonical_location_id",
            sa.String(50),
            sa.ForeignKey("locations.canonical_location_id"),
            nullable=False,
        ),
        sa.Column("accepted_qty_base", sa.Numeric(15, 4), nullable=False),
        sa.Column("posting_status", sa.String(50), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("source_system", "source_record_id"),
        sa.UniqueConstraint("import_batch_id", "receipt_number", "receipt_line"),
    )

    # Employees
    op.create_table(
        "employees",
        sa.Column("source_employee_id", sa.String(50), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("employment_status", sa.String(50), nullable=False),
        sa.Column("role", sa.String(100), nullable=False),
        sa.Column(
            "canonical_location_id",
            sa.String(50),
            sa.ForeignKey("locations.canonical_location_id"),
            nullable=True,
        ),
        sa.Column(
            "manager_employee_id",
            sa.String(50),
            sa.ForeignKey("employees.source_employee_id"),
            nullable=True,
        ),
        sa.Column("region_id", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Workforce Status
    op.create_table(
        "workforce_status",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column(
            "source_employee_id",
            sa.String(50),
            sa.ForeignKey("employees.source_employee_id"),
            nullable=False,
        ),
        sa.Column("availability_status", sa.String(50), nullable=False),
        sa.Column("shift_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shift_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("import_batch_id", sa.Uuid, sa.ForeignKey("import_batches.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("source_employee_id", "observed_at"),
    )

    # Exceptions (business cases)
    op.create_table(
        "exceptions",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("exception_type", sa.String(100), nullable=False),
        sa.Column("business_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="OPEN"),
        sa.Column(
            "detection_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("po_number", sa.String(50), nullable=True),
        sa.Column("po_line_number", sa.Integer, nullable=True),
        sa.Column(
            "canonical_product_id",
            sa.String(50),
            sa.ForeignKey("products.canonical_product_id"),
            nullable=False,
        ),
        sa.Column(
            "canonical_location_id",
            sa.String(50),
            sa.ForeignKey("locations.canonical_location_id"),
            nullable=False,
        ),
        sa.Column("expected_qty_base", sa.Numeric(15, 4), nullable=False),
        sa.Column("received_qty_base", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("variance_base", sa.Numeric(15, 4), nullable=False),
        sa.Column("base_uom", sa.String(20), nullable=False),
        sa.Column("rule_version", sa.String(50), nullable=False),
        sa.Column("mapping_version", sa.String(50), nullable=False),
        sa.Column("explanation", sa.Text, nullable=False),
        sa.Column("resolution_code", sa.String(100), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("exception_type", "business_key"),
    )

    # Exception Evidence (links to source records)
    op.create_table(
        "exception_evidence",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("exception_id", sa.Uuid, sa.ForeignKey("exceptions.id"), nullable=False),
        sa.Column("source_system", sa.String(50), nullable=False),
        sa.Column("source_record_id", sa.String(100), nullable=False),
        sa.Column("record_type", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("exception_id", "source_system", "source_record_id"),
    )

    # Tasks
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("task_type", sa.String(100), nullable=False),
        sa.Column("exception_id", sa.Uuid, sa.ForeignKey("exceptions.id"), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="CREATED"),
        sa.Column(
            "assignee_employee_id",
            sa.String(50),
            sa.ForeignKey("employees.source_employee_id"),
            nullable=True,
        ),
        sa.Column(
            "assigned_by_employee_id",
            sa.String(50),
            sa.ForeignKey("employees.source_employee_id"),
            nullable=True,
        ),
        sa.Column("assignment_reason", sa.Text, nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_code", sa.String(100), nullable=True),
        sa.Column("evidence_reference", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("exception_id", "task_type"),
    )

    # Task Audit Events
    op.create_table(
        "task_audit_events",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("task_id", sa.Uuid, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("from_status", sa.String(50), nullable=True),
        sa.Column("to_status", sa.String(50), nullable=True),
        sa.Column(
            "actor_employee_id",
            sa.String(50),
            sa.ForeignKey("employees.source_employee_id"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("metadata", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Indexes for common queries
    op.create_index("idx_po_lines_po_number", "po_lines", ["po_number"])
    op.create_index("idx_receipt_lines_po_number", "receipt_lines", ["po_number"])
    op.create_index("idx_receipt_lines_po_line", "receipt_lines", ["po_number", "po_line_number"])
    op.create_index("idx_tasks_assignee", "tasks", ["assignee_employee_id"])
    op.create_index("idx_tasks_exception", "tasks", ["exception_id"])
    op.create_index("idx_tasks_status", "tasks", ["status"])
    op.create_index("idx_exceptions_status", "exceptions", ["status"])
    op.create_index("idx_exceptions_po", "exceptions", ["po_number"])


def downgrade() -> None:
    op.drop_index("idx_exceptions_po", table_name="exceptions")
    op.drop_index("idx_exceptions_status", table_name="exceptions")
    op.drop_index("idx_tasks_status", table_name="tasks")
    op.drop_index("idx_tasks_exception", table_name="tasks")
    op.drop_index("idx_tasks_assignee", table_name="tasks")
    op.drop_index("idx_receipt_lines_po_line", table_name="receipt_lines")
    op.drop_index("idx_receipt_lines_po_number", table_name="receipt_lines")
    op.drop_index("idx_po_lines_po_number", table_name="po_lines")
    op.drop_table("task_audit_events")
    op.drop_table("tasks")
    op.drop_table("exception_evidence")
    op.drop_table("exceptions")
    op.drop_table("workforce_status")
    op.drop_table("employees")
    op.drop_table("receipt_lines")
    op.drop_table("po_lines")
    op.drop_table("import_errors")
    op.drop_table("import_batches")
    op.drop_table("location_aliases")
    op.drop_table("product_aliases")
    op.drop_table("locations")
    op.drop_table("products")
