# OPSiB Backend Engineering Assessment - Analysis & Implementation

## Phase 1: Requirements Analysis

### 1. Requirement Checklist

#### Task A: Ingestion, Mapping, and Persistence
- [ ] Load and validate all 6 CSV datasets
- [ ] Report accepted/rejected/duplicate rows with reasons
- [ ] Normalize product IDs through aliases
- [ ] Normalize location IDs through aliases
- [ ] Convert quantities to canonical base UOM
- [ ] Preserve provenance (source system, record ID, import reference, rule version)
- [ ] Idempotent ingestion - no double-counting
- [ ] Detect and flag conflicting duplicate source IDs
- [ ] Keep business state separate from raw input
- [ ] Use typed validation and timezone-aware parsing
- [ ] Use frozen evaluation time, not machine clock
- [ ] Database constraints/transactions for invariants
- [ ] Provide usable import result with partial success visibility

#### Task B: Reconciliation and Exception Detection
- [ ] Deterministic receipt-shortfall rule
- [ ] Use mapped product/location identity
- [ ] Consider source status (PO status)
- [ ] Consider receipt posting status
- [ ] Compare quantities in comparable units
- [ ] Consider receipt timing (deadline comparison)
- [ ] Duplicate-safe aggregation
- [ ] Zero tolerance (configurable)
- [ ] Incomplete/ambiguous mapping = data quality issue, not exception
- [ ] Required outcomes:
  - PO-501: 8 EA shortfall
  - PO-502: no shortfall
  - PO-503: no actionable shortfall (future deadline)
  - PO-504: no shortfall (cancelled)
  - PO-505: 24 EA shortfall (BOX→EA conversion)
  - PO-506: 10 EA shortfall (duplicate counted once)
- [ ] Findings contain: stable ID, business key, type, status, detection timestamp
- [ ] Findings contain: PO/line, product, location, expected, received, variance
- [ ] Findings contain: source record IDs, rule/mapping versions
- [ ] Neutral explanation - no supplier fault claims
- [ ] Path for un-evaluable cases

#### Task C: Employee Routing and Task Lifecycle
- [ ] Separate exception (business case) from task (work)
- [ ] One exception may have multiple tasks
- [ ] Task reassignment doesn't create new exception
- [ ] Primary role for shortfalls: receiving_associate at same location
- [ ] Employee must be ACTIVE
- [ ] If using workforce status: must be ON_SHIFT at frozen time
- [ ] Availability sample must be fresh enough
- [ ] HR-101: OFF_SHIFT - not assigned
- [ ] HR-103: INACTIVE - not assigned (despite ON_SHIFT)
- [ ] HR-102: eligible on-shift receiver at STORE-014
- [ ] STORE-015: no receiver → fallback to store manager HR-401
- [ ] No eligible fallback → keep task visible for manual assignment
- [ ] Persist why person/queue selected
- [ ] Preserve assignment history
- [ ] Server-enforced state machine (no arbitrary client transitions)
- [ ] State machine: CREATED → ASSIGNED → ACKNOWLEDGED → IN_PROGRESS → COMPLETED → PENDING_VERIFICATION → VERIFIED
- [ ] Document blocked/rejected/reopened behavior
- [ ] List exceptions with source records
- [ ] List tasks by assignee/location with status/due time
- [ ] Acknowledge/start/complete through validated transitions
- [ ] Reassign with reason and recorded changer
- [ ] Resolution code and evidence reference
- [ ] Mark exception resolved only when verification criteria met
- [ ] Audit/event history for state transitions and assignment changes
- [ ] Safe task completion retries (idempotent)

#### Task D: Reliability, Tests, AI Boundary
- [ ] Database transactions for source identities and task creation
- [ ] Uniqueness constraints prevent duplicates
- [ ] Explain concurrent/retried processing avoidance
- [ ] Handle conflicting duplicate source IDs explicitly
- [ ] Identical repeat = idempotent
- [ ] Same ID with changed content = documented conflict policy
- [ ] Timezone-aware timestamps
- [ ] Frozen time for deadline test
- [ ] Useful API errors and structured logs
- [ ] No credentials or unnecessary PII in logs
- [ ] Production hardening notes (auth, secrets, migrations, monitoring, retention)
- [ ] Unit tests: UOM conversion, partial receipts, deadline boundaries, cancelled POs
- [ ] Integration test: load fixture pack, verify 6 scenarios
- [ ] Idempotency test: import twice, assert no change
- [ ] Identity/assignment tests: location mapping, off-shift, inactive, fallback
- [ ] Workflow tests: invalid transitions, repeated completion, audit history
- [ ] Failure-path test: unknown mapping, conflicting duplicate, absent assignee
- [ ] Optional AI provider interface (no live key needed)
- [ ] AI may explain evidence, identify missing context, suggest steps
- [ ] AI must NOT: calculate quantities, invent records, decide permissions, write ERP, mark verified
- [ ] Validate structured AI output
- [ ] Application functional if AI times out or returns invalid data
- [ ] AI receives only necessary case context
- [ ] Check cited record IDs against evidence

### 2. CSV Inventory

#### purchase_order_lines.csv
- **Source System**: ERP
- **Columns**:
  - `source_record_id` (string): Unique identifier for this source row
  - `po_number` (string): Purchase order number (e.g., PO-501)
  - `line_number` (integer): Line number within PO
  - `supplier_code` (string): Supplier identifier
  - `location_code` (string): ERP location code (needs mapping)
  - `item_code` (string): ERP product/item code (needs mapping)
  - `ordered_qty` (decimal): Quantity ordered
  - `ordered_uom` (string): Unit of measure for ordered quantity
  - `status` (string): PO line status (OPEN, CANCELLED, etc.)
  - `expected_receipt_by` (datetime): Deadline for receipt (timezone-aware)
- **Data Quality**:
  - 7 rows total, 1 identical duplicate (ERP-PO506-L1 appears twice)
  - PO-504 has status=CANCELLED
  - PO-503 has future deadline (2026-10-17T18:00:00+05:30)
  - PO-505 uses BOX UOM

#### goods_receipt_lines.csv
- **Source System**: WMS
- **Columns**:
  - `source_record_id` (string): Unique identifier for this source row
  - `receipt_number` (string): Goods receipt number (e.g., GRN-501-A)
  - `receipt_line` (integer): Line number within receipt
  - `po_number` (string): PO number this receipt is for
  - `po_line_number` (integer): PO line number this receipt is for
  - `location_code` (string): WMS location code (needs mapping)
  - `product_ref` (string): WMS product reference (needs mapping)
  - `accepted_qty` (decimal): Quantity accepted
  - `uom` (string): Unit of measure for accepted quantity
  - `received_at` (datetime): When receipt occurred (timezone-aware)
  - `posting_status` (string): POSTED or other status
- **Data Quality**:
  - 7 rows total, 1 identical duplicate (WMS-GRN506-A-L1 appears twice)
  - Receipts split across GRNs for PO-501 (GRN-501-A and GRN-501-B)
  - All receipts have posting_status=POSTED

#### product_aliases.csv
- **Source System**: N/A (mapping reference)
- **Columns**:
  - `source_system` (string): ERP, WMS, etc.
  - `external_item_id` (string): Product ID in source system
  - `canonical_product_id` (string): Canonical product identifier
  - `source_uom` (string): UOM in source system
  - `base_uom` (string): Canonical base UOM
  - `to_base_factor` (decimal): Conversion factor to base UOM
- **Mappings**:
  - ERP-SKU-001 → SKU-001 (EA→EA, factor 1)
  - WMS-BRC-001 → SKU-001 (EA→EA, factor 1)
  - ERP-BOX-12 → SKU-002 (BOX→EA, factor 12)
  - WMS-MAT-2002 → SKU-002 (EA→EA, factor 1) [Note: This says EA but maps to SKU-002, same as BOX-12]
  - ERP-SKU-003 → SKU-003 (EA→EA, factor 1)
  - WMS-BRC-003 → SKU-003 (EA→EA, factor 1)

#### location_aliases.csv
- **Source System**: N/A (mapping reference)
- **Columns**:
  - `source_system` (string): ERP, WMS, HRMS
  - `external_location_id` (string): Location ID in source system
  - `canonical_location_id` (string): Canonical location identifier
  - `region_id` (string): Region identifier
- **Mappings**:
  - ERP MUM-014 → STORE-014 (REGION-MH)
  - WMS WH-MH-014 → STORE-014 (REGION-MH)
  - HRMS ST-014 → STORE-014 (REGION-MH)
  - ERP MUM-015 → STORE-015 (REGION-MH)
  - WMS WH-MH-015 → STORE-015 (REGION-MH)
  - HRMS ST-015 → STORE-015 (REGION-MH)

#### employees.csv
- **Source System**: HRMS
- **Columns**:
  - `source_employee_id` (string): Employee ID
  - `display_name` (string): Employee name (not for matching)
  - `employment_status` (string): ACTIVE, INACTIVE
  - `role` (string): receiving_associate, store_manager, regional_operations
  - `location_code` (string): HRMS location code (needs mapping)
  - `manager_employee_id` (string): Manager's employee ID (nullable)
  - `region_id` (string): Region identifier
- **Data Quality**:
  - 6 employees
  - HR-103 is INACTIVE
  - HR-501 (regional_operations) has no location_code (region-scoped)
  - Don't use display_name for matching

#### workforce_status.csv
- **Source System**: Workforce management
- **Columns**:
  - `source_employee_id` (string): Employee ID
  - `availability_status` (string): ON_SHIFT, OFF_SHIFT
  - `shift_start` (datetime): Shift start time
  - `shift_end` (datetime): Shift end time
  - `observed_at` (datetime): When observation was made
- **Data Quality**:
  - 6 records, all observed_at=2026-10-16T14:50:00+05:30
  - HR-101 is OFF_SHIFT
  - HR-103 is ON_SHIFT (but INACTIVE in employees.csv)
  - All others ON_SHIFT

### 3. Canonical Entities and Relationships

#### Product
- **Primary Key**: canonical_product_id
- **Attributes**: display_name (optional), base_uom, status
- **Relationships**:
  - One-to-many with ProductAlias (source mappings)
  - One-to-many with POLine (what was ordered)
  - One-to-many with ReceiptLine (what was received)

#### Location
- **Primary Key**: canonical_location_id
- **Attributes**: region_id, status
- **Relationships**:
  - One-to-many with LocationAlias (source mappings)
  - One-to-many with POLine (where ordered)
  - One-to-many with ReceiptLine (where received)
  - One-to-many with Employee (assigned location, optional)

#### POLine
- **Primary Key**: (source_system, source_record_id) or surrogate key
- **Business Key**: po_number + line_number (within import batch)
- **Attributes**:
  - Source identity (system, record_id)
  - PO number, line number
  - Supplier code
  - Ordered quantity in base UOM
  - Status (OPEN, CANCELLED, etc.)
  - Expected receipt deadline
  - Import reference
  - Mapping version
- **Relationships**:
  - Many-to-one with Product (via alias mapping)
  - Many-to-one with Location (via alias mapping)
  - One-to-many with ReceiptLine (receipts for this line)
  - One-to-many with Exception (shortfall findings)

#### ReceiptLine
- **Primary Key**: (source_system, source_record_id)
- **Business Key**: receipt_number + receipt_line
- **Attributes**:
  - Source identity (system, record_id)
  - Receipt number, line number
  - PO number, PO line number (references POLine)
  - Accepted quantity in base UOM
  - Posting status
  - Received timestamp
  - Import reference
  - Mapping version
- **Relationships**:
  - Many-to-one with Product (via alias mapping)
  - Many-to-one with Location (via alias mapping)
  - Many-to-one with POLine (what PO line this receipt is for)
  - One-to-many with Exception (evidence for findings)

#### Employee
- **Primary Key**: source_employee_id
- **Attributes**:
  - Display name (for display only, not matching)
  - Employment status (ACTIVE, INACTIVE)
  - Role (receiving_associate, store_manager, regional_operations)
  - Canonical location (nullable - region-scoped employees may not have one)
  - Manager ID (nullable, self-reference)
  - Region ID
- **Relationships**:
  - Many-to-one with Location (optional, via alias mapping)
  - One-to-many with WorkforceStatus (availability snapshots)
  - One-to-many with Task (assignments)

#### WorkforceStatus
- **Primary Key**: (source_employee_id, observed_at)
- **Attributes**:
  - Availability status (ON_SHIFT, OFF_SHIFT)
  - Shift start/end times
  - Observed timestamp
- **Relationships**:
  - Many-to-one with Employee

#### Exception
- **Primary Key**: surrogate key
- **Business Key**: Stable case key (deterministic from evidence)
- **Attributes**:
  - Type (e.g., RECEIPT_SHORTFALL)
  - Status (OPEN, INVESTIGATING, RESOLVED, etc.)
  - Detection timestamp
  - PO/line reference
  - Canonical product and location
  - Expected quantity (base UOM)
  - Received quantity (base UOM)
  - Variance
  - Unit
  - Source record IDs (evidence)
  - Rule version
  - Mapping version
  - Neutral explanation
- **Relationships**:
  - One-to-many with Task
  - One-to-many with ExceptionEvidence (linked source records)

#### Task
- **Primary Key**: surrogate key
- **Business Key**: Exception ID + task type/role
- **Attributes**:
  - Exception ID (reference)
  - Type/role (e.g., INVESTIGATE_SHORTFALL)
  - Status (CREATED, ASSIGNED, ACKNOWLEDGED, IN_PROGRESS, COMPLETED, PENDING_VERIFICATION, VERIFIED)
  - Assignee ID ( nullable - may need manual assignment)
  - Assigned by (who made the assignment)
  - Assignment reason
  - Due time
  - Resolution code (when completed)
  - Evidence reference (when completed)
  - Created timestamp
  - Updated timestamp
- **Relationships**:
  - Many-to-one with Exception
  - Many-to-one with Employee (assignee)
  - One-to-many with TaskAuditEvent

#### TaskAuditEvent
- **Primary Key**: surrogate key
- **Attributes**:
  - Task ID
  - Event type (ASSIGNED, ACKNOWLEDGED, STATE_CHANGE, REASSIGNED, etc.)
  - From status (for state changes)
  - To status (for state changes)
  - Actor (who made the change)
  - Reason/notes
  - Timestamp

### 4. Product Alias Mapping Table

| Source System | External Item ID | Canonical Product | Source UOM | Base UOM | Factor |
|--------------|------------------|-------------------|------------|----------|--------|
| ERP | ERP-SKU-001 | SKU-001 | EA | EA | 1 |
| WMS | WMS-BRC-001 | SKU-001 | EA | EA | 1 |
| ERP | ERP-BOX-12 | SKU-002 | BOX | EA | 12 |
| WMS | WMS-MAT-2002 | SKU-002 | EA | EA | 1 |
| ERP | ERP-SKU-003 | SKU-003 | EA | EA | 1 |
| WMS | WMS-BRC-003 | SKU-003 | EA | EA | 1 |

**Note**: WMS-MAT-2002 maps to SKU-002 with EA→EA factor 1, while ERP-BOX-12 maps to SKU-002 with BOX→EA factor 12. This needs careful handling - these are different source UOMs that both map to the same canonical product. The factor determines how to convert TO the base UOM.

### 5. Location Alias Mapping Table

| Source System | External Location ID | Canonical Location | Region |
|--------------|---------------------|-------------------|--------|
| ERP | MUM-014 | STORE-014 | REGION-MH |
| WMS | WH-MH-014 | STORE-014 | REGION-MH |
| HRMS | ST-014 | STORE-014 | REGION-MH |
| ERP | MUM-015 | STORE-015 | REGION-MH |
| WMS | WH-MH-015 | STORE-015 | REGION-MH |
| HRMS | ST-015 | STORE-015 | REGION-MH |

### 6. Duplicate Source Identities

#### Purchase Order Lines
- **ERP-PO506-L1**: Appears twice with identical content
  - Row 1: PO-506, line 1, SUP-22, MUM-014, ERP-SKU-003, 20 EA, OPEN, 2026-10-15T15:00:00+05:30
  - Row 2: PO-506, line 1, SUP-22, MUM-014, ERP-SKU-003, 20 EA, OPEN, 2026-10-15T15:00:00+05:30
  - **Verdict**: IDENTICAL - idempotent, count once

#### Goods Receipt Lines
- **WMS-GRN506-A-L1**: Appears twice with identical content
  - Row 1: GRN-506-A, line 1, PO-506, line 1, WH-MH-014, WMS-BRC-003, 10 EA, 2026-10-15T11:00:00+05:30, POSTED
  - Row 2: GRN-506-A, line 1, PO-506, line 1, WH-MH-014, WMS-BRC-003, 10 EA, 2026-10-15T11:00:00+05:30, POSTED
  - **Verdict**: IDENTICAL - idempotent, count once

### 7. Frozen Evaluation Timestamp

**Value**: 2026-10-16T15:00:00+05:30 (Asia/Kolkata)

**Timezone Handling**:
- All datetimes in CSVs are timezone-aware with +05:30 offset
- Store all timestamps in UTC in database
- Convert frozen time to UTC for comparisons: 2026-10-16T09:30:00Z
- Use timezone-aware datetime objects throughout
- Deadline comparisons: `received_at <= frozen_eval_time` or `expected_receipt_by <= frozen_eval_time`

### 8. Six Required PO Outcomes (with Calculations)

#### PO-501: 8 EA shortfall ✓
- **Ordered**: 100 EA (ERP-SKU-001 → SKU-001, base UOM EA, factor 1)
  - Source: ERP-PO501-L1, 100 EA × 1 = 100 EA
- **Received**:
  - GRN-501-A: 60 EA (WMS-BRC-001 → SKU-001, factor 1) → 60 EA
  - GRN-501-B: 32 EA (WMS-BRC-001 → SKU-001, factor 1) → 32 EA
  - Total: 60 + 32 = 92 EA
- **Deadline**: 2026-10-15T12:00:00+05:30 (passed at frozen time)
- **Variance**: 100 - 92 = 8 EA shortfall
- **Result**: ACTIONABLE SHORTFALL

#### PO-502: No shortfall ✓
- **Ordered**: 40 EA (ERP-SKU-001 → SKU-001)
  - Source: ERP-PO502-L1, 40 EA × 1 = 40 EA
- **Received**:
  - GRN-502-A: 40 EA (WMS-BRC-001 → SKU-001) → 40 EA
  - Total: 40 EA
- **Deadline**: 2026-10-15T12:00:00+05:30 (passed)
- **Variance**: 40 - 40 = 0 EA
- **Result**: NO SHORTFALL

#### PO-503: No actionable shortfall at frozen time ✓
- **Ordered**: 50 EA (ERP-SKU-001 → SKU-001)
  - Source: ERP-PO503-L1, 50 EA × 1 = 50 EA
- **Received**:
  - GRN-503-A: 20 EA (WMS-BRC-001 → SKU-001) → 20 EA
  - Total: 20 EA
- **Deadline**: 2026-10-17T18:00:00+05:30 (FUTURE at frozen time 2026-10-16T15:00:00+05:30)
- **Variance**: 50 - 20 = 30 EA (but deadline not passed)
- **Result**: NO ACTIONABLE SHORTFALL (deadline still in future)

#### PO-504: No shortfall (cancelled) ✓
- **Ordered**: 20 EA (ERP-SKU-001 → SKU-001)
  - Source: ERP-PO504-L1, 20 EA × 1 = 20 EA
- **Status**: CANCELLED
- **Result**: NO SHORTFALL (cancelled orders don't generate findings)

#### PO-505: 24 EA shortfall ✓
- **Ordered**: 10 BOX (ERP-BOX-12 → SKU-002, BOX→EA, factor 12)
  - Source: ERP-PO505-L1, 10 BOX × 12 = 120 EA
- **Received**:
  - GRN-505-A: 96 EA (WMS-MAT-2002 → SKU-002, EA→EA, factor 1) → 96 EA
  - Total: 96 EA
- **Deadline**: 2026-10-15T15:00:00+05:30 (passed)
- **Variance**: 120 - 96 = 24 EA shortfall
- **Result**: ACTIONABLE SHORTFALL

#### PO-506: 10 EA shortfall (duplicate counted once) ✓
- **Ordered**: 20 EA (ERP-SKU-003 → SKU-003, EA→EA, factor 1)
  - Source: ERP-PO506-L1 (appears twice, identical, count once), 20 EA × 1 = 20 EA
- **Received**:
  - GRN-506-A: 10 EA (WMS-BRC-003 → SKU-003, EA→EA, factor 1) → 10 EA
  - WMS-GRN506-A-L1 appears twice, identical, count once → 10 EA total
- **Deadline**: 2026-10-15T15:00:00+05:30 (passed)
- **Variance**: 20 - 10 = 10 EA shortfall
- **Result**: ACTIONABLE SHORTFALL (duplicate counted once)

### 9. Expected Employee Routing Outcomes

#### STORE-014 Shortfall Tasks (PO-501, PO-502 if needed, PO-506)
- **Eligible receiving associates at STORE-014**:
  - HR-101 (Asha Rao): ACTIVE but OFF_SHIFT → NOT ELIGIBLE
  - HR-102 (Dev Shah): ACTIVE and ON_SHIFT → **ELIGIBLE ✓**
  - HR-103 (Nikhil Das): INACTIVE (despite ON_SHIFT) → NOT ELIGIBLE
- **Selected assignee**: HR-102 (Dev Shah)
- **Rationale**: Only active, on-shift receiving associate at the location

#### STORE-015 Shortfall Tasks (PO-505)
- **Eligible receiving associates at STORE-015**: NONE
- **Fallback**: Store manager at STORE-015
  - HR-401 (Rohan Iyer): ACTIVE, ON_SHIFT, store_manager at ST-015 → **ELIGIBLE ✓
  - HR-201 (Meera Kulkarni): ACTIVE, ON_SHIFT, store_manager at ST-014 (different location) → NOT for STORE-015
- **Selected assignee**: HR-401 (Rohan Iyer)
- **Rationale**: No receiving associate available, fallback to on-shift store manager

#### Regional Employees
- HR-501 (Priya Menon): ACTIVE, ON_SHIFT, regional_operations
  - No location_code (region-scoped) → NOT assigned to store-specific tasks
  - Could be used for regional oversight tasks if implemented

### 10. Proposed PostgreSQL Schema

```sql
-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Canonical Products
CREATE TABLE products (
    canonical_product_id VARCHAR(50) PRIMARY KEY,
    display_name VARCHAR(255),
    base_uom VARCHAR(20) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Canonical Locations
CREATE TABLE locations (
    canonical_location_id VARCHAR(50) PRIMARY KEY,
    region_id VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Product Aliases (source → canonical mapping)
CREATE TABLE product_aliases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_system VARCHAR(50) NOT NULL,
    external_item_id VARCHAR(100) NOT NULL,
    canonical_product_id VARCHAR(50) NOT NULL REFERENCES products(canonical_product_id),
    source_uom VARCHAR(20) NOT NULL,
    base_uom VARCHAR(20) NOT NULL,
    to_base_factor DECIMAL(15,4) NOT NULL,
    mapping_version VARCHAR(50) NOT NULL DEFAULT '1.0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_system, external_item_id, mapping_version)
);

-- Location Aliases (source → canonical mapping)
CREATE TABLE location_aliases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_system VARCHAR(50) NOT NULL,
    external_location_id VARCHAR(100) NOT NULL,
    canonical_location_id VARCHAR(50) NOT NULL REFERENCES locations(canonical_location_id),
    region_id VARCHAR(50) NOT NULL,
    mapping_version VARCHAR(50) NOT NULL DEFAULT '1.0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_system, external_location_id, mapping_version)
);

-- Import Batches
CREATE TABLE import_batches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    dataset_name VARCHAR(100) NOT NULL,
    import_reference VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'PROCESSING',
    total_rows INTEGER NOT NULL DEFAULT 0,
    accepted_rows INTEGER NOT NULL DEFAULT 0,
    rejected_rows INTEGER NOT NULL DEFAULT 0,
    duplicate_rows INTEGER NOT NULL DEFAULT 0,
    conflicting_rows INTEGER NOT NULL DEFAULT 0,
    rule_version VARCHAR(50) NOT NULL,
    mapping_version VARCHAR(50) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error_message TEXT
);

-- Import Errors (row-level)
CREATE TABLE import_errors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    import_batch_id UUID NOT NULL REFERENCES import_batches(id),
    source_record_id VARCHAR(100),
    row_number INTEGER,
    error_code VARCHAR(100) NOT NULL,
    error_message TEXT NOT NULL,
    raw_data JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Purchase Order Lines
CREATE TABLE po_lines (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_system VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(100) NOT NULL,
    import_batch_id UUID NOT NULL REFERENCES import_batches(id),
    po_number VARCHAR(50) NOT NULL,
    line_number INTEGER NOT NULL,
    supplier_code VARCHAR(50),
    canonical_product_id VARCHAR(50) NOT NULL REFERENCES products(canonical_product_id),
    canonical_location_id VARCHAR(50) NOT NULL REFERENCES locations(canonical_location_id),
    ordered_qty_base DECIMAL(15,4) NOT NULL,
    status VARCHAR(50) NOT NULL,
    expected_receipt_by TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_system, source_record_id),
    UNIQUE (import_batch_id, po_number, line_number)
);

-- Goods Receipt Lines
CREATE TABLE receipt_lines (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_system VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(100) NOT NULL,
    import_batch_id UUID NOT NULL REFERENCES import_batches(id),
    receipt_number VARCHAR(50) NOT NULL,
    receipt_line INTEGER NOT NULL,
    po_number VARCHAR(50) NOT NULL,
    po_line_number INTEGER NOT NULL,
    canonical_product_id VARCHAR(50) NOT NULL REFERENCES products(canonical_product_id),
    canonical_location_id VARCHAR(50) NOT NULL REFERENCES locations(canonical_location_id),
    accepted_qty_base DECIMAL(15,4) NOT NULL,
    posting_status VARCHAR(50) NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_system, source_record_id),
    UNIQUE (import_batch_id, receipt_number, receipt_line)
);

-- Employees
CREATE TABLE employees (
    source_employee_id VARCHAR(50) PRIMARY KEY,
    display_name VARCHAR(255) NOT NULL,
    employment_status VARCHAR(50) NOT NULL,
    role VARCHAR(100) NOT NULL,
    canonical_location_id VARCHAR(50) REFERENCES locations(canonical_location_id),
    manager_employee_id VARCHAR(50) REFERENCES employees(source_employee_id),
    region_id VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Workforce Status
CREATE TABLE workforce_status (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_employee_id VARCHAR(50) NOT NULL REFERENCES employees(source_employee_id),
    availability_status VARCHAR(50) NOT NULL,
    shift_start TIMESTAMPTZ NOT NULL,
    shift_end TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    import_batch_id UUID NOT NULL REFERENCES import_batches(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_employee_id, observed_at)
);

-- Exceptions (business cases)
CREATE TABLE exceptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    exception_type VARCHAR(100) NOT NULL,
    business_key VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'OPEN',
    detection_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    po_number VARCHAR(50),
    po_line_number INTEGER,
    canonical_product_id VARCHAR(50) NOT NULL REFERENCES products(canonical_product_id),
    canonical_location_id VARCHAR(50) NOT NULL REFERENCES locations(canonical_location_id),
    expected_qty_base DECIMAL(15,4) NOT NULL,
    received_qty_base DECIMAL(15,4) NOT NULL DEFAULT 0,
    variance_base DECIMAL(15,4) NOT NULL,
    base_uom VARCHAR(20) NOT NULL,
    rule_version VARCHAR(50) NOT NULL,
    mapping_version VARCHAR(50) NOT NULL,
    explanation TEXT NOT NULL,
    resolution_code VARCHAR(100),
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (exception_type, business_key)
);

-- Exception Evidence (links to source records)
CREATE TABLE exception_evidence (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    exception_id UUID NOT NULL REFERENCES exceptions(id),
    source_system VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(100) NOT NULL,
    record_type VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (exception_id, source_system, source_record_id)
);

-- Tasks
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_type VARCHAR(100) NOT NULL,
    exception_id UUID NOT NULL REFERENCES exceptions(id),
    status VARCHAR(50) NOT NULL DEFAULT 'CREATED',
    assignee_employee_id VARCHAR(50) REFERENCES employees(source_employee_id),
    assigned_by_employee_id VARCHAR(50) REFERENCES employees(source_employee_id),
    assignment_reason TEXT,
    due_at TIMESTAMPTZ,
    resolution_code VARCHAR(100),
    evidence_reference TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (exception_id, task_type)
);

-- Task Audit Events
CREATE TABLE task_audit_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL REFERENCES tasks(id),
    event_type VARCHAR(100) NOT NULL,
    from_status VARCHAR(50),
    to_status VARCHAR(50),
    actor_employee_id VARCHAR(50) REFERENCES employees(source_employee_id),
    reason TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_po_lines_po_number ON po_lines(po_number);
CREATE INDEX idx_receipt_lines_po_number ON receipt_lines(po_number);
CREATE INDEX idx_receipt_lines_po_line ON receipt_lines(po_number, po_line_number);
CREATE INDEX idx_tasks_assignee ON tasks(assignee_employee_id);
CREATE INDEX idx_tasks_exception ON tasks(exception_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_exceptions_status ON exceptions(status);
CREATE INDEX idx_exceptions_po ON exceptions(po_number);
```

### 11. API Contract

#### Import Endpoints

**POST /api/v1/imports/{dataset}**
- Import a dataset (po_lines, receipt_lines, product_aliases, location_aliases, employees, workforce_status)
- Request: multipart/form-data with file, or JSON with file content
- Response: ImportBatch with status, counts, errors

**GET /api/v1/imports/{import_id}**
- Get import batch status and details
- Response: ImportBatch with row counts, errors, timestamps

**GET /api/v1/data-quality**
- Get data quality report
- Response: Unresolved mappings, rejected records, conflicts

#### Reconciliation Endpoints

**POST /api/v1/reconciliation/run**
- Run reconciliation with frozen evaluation time
- Query params: evaluation_time (optional, defaults to frozen time)
- Response: ReconciliationResult with findings

**GET /api/v1/exceptions**
- List exceptions with filtering
- Query params: status, po_number, location_id, assignee_id
- Response: List of exceptions with summary

**GET /api/v1/exceptions/{exception_id}**
- Get exception detail with source evidence
- Response: Exception with linked source records

#### Task Endpoints

**GET /api/v1/tasks**
- List tasks with filtering
- Query params: assignee_id, location_id, status, exception_id
- Response: List of tasks

**GET /api/v1/tasks/{task_id}**
- Get task detail with audit history
- Response: Task with audit events

**POST /api/v1/tasks/{task_id}/acknowledge**
- Acknowledge a task
- Response: Updated task

**POST /api/v1/tasks/{task_id}/start**
- Start working on a task
- Response: Updated task

**POST /api/v1/tasks/{task_id}/complete**
- Complete a task
- Request body: { resolution_code, evidence_reference }
- Response: Updated task

**POST /api/v1/tasks/{task_id}/reassign**
- Reassign a task
- Request body: { assignee_employee_id, reason }
- Response: Updated task

#### Health Endpoint

**GET /health**
- Health check
- Response: { "status": "healthy", "database": "connected", "frozen_evaluation_time": "..." }

### 12. Test Matrix

| Assessment Requirement | Test Type | Test Description |
|----------------------|-----------|------------------|
| UOM conversion | Unit | Test BOX→EA conversion (factor 12) |
| Partial receipts | Unit | Test aggregation of multiple receipt lines |
| Deadline boundaries | Unit | Test future vs past deadline logic |
| Cancelled PO | Unit | Test cancelled PO doesn't create finding |
| Fixture integration | Integration | Load all 6 CSVs, verify 6 scenarios |
| PO-501: 8 EA shortfall | Integration | Verify exact shortfall calculation |
| PO-502: no shortfall | Integration | Verify zero variance = no exception |
| PO-503: future deadline | Integration | Verify no actionable finding |
| PO-504: cancelled | Integration | Verify cancelled = no finding |
| PO-505: 24 EA (BOX conversion) | Integration | Verify UOM conversion in calculation |
| PO-506: 10 EA (duplicate) | Integration | Verify duplicate counted once |
| Idempotency - import twice | Integration | Import same data twice, verify no change |
| Duplicate source - identical | Unit/Integration | Identical duplicates handled idempotently |
| Duplicate source - conflicting | Unit/Integration | Conflicting duplicates flagged for review |
| Employee routing - HR-102 | Integration | Active, on-shift receiver selected |
| Employee routing - HR-101 off-shift | Integration | Off-shift employee not assigned |
| Employee routing - HR-103 inactive | Integration | Inactive employee not assigned |
| Employee routing - HR-401 fallback | Integration | Store manager fallback for STORE-015 |
| Employee routing - no eligible | Integration | Task stays unassigned if no one eligible |
| State machine - invalid transition | Unit/Integration | Reject invalid state transitions |
| State machine - valid transitions | Unit/Integration | Allow valid state transitions |
| Task completion retry | Integration | Safe to retry completion (idempotent) |
| Audit history | Integration | Verify audit events recorded |
| Unknown product mapping | Integration | Data quality issue, not exception |
| Unknown location mapping | Integration | Data quality issue, not exception |
| Conflicting duplicate ID | Integration | Reported for review, not silently overwritten |
| Health endpoint | Integration | Verify health check works |
| API error responses | Integration | Verify structured error responses |

### 13. Explicit Assumptions

1. **Frozen evaluation time**: 2026-10-16T15:00:00+05:30 is the single source of truth for deadline comparisons. Not configurable per-request in basic implementation.

2. **Shift freshness**: Workforce status observed_at=2026-10-16T14:50:00+05:30 is considered fresh enough (within 1 hour of frozen time). Policy: observations within 2 hours are acceptable.

3. **Conflict handling**: Identical duplicates are idempotent (ignored on re-import). Conflicting duplicates (same source_record_id, different business content) are rejected and flagged for review - no silent overwrite.

4. **Receipt aggregation**: Receipts are aggregated by PO line using source identity. Each unique source_record_id is counted once. Posting status must be POSTED to count.

5. **UOM conversion**: Conversion happens at ingestion time. Quantities are stored in base UOM in the database. The to_base_factor from product_aliases is used for conversion.

6. **Employee location mapping**: Employee location_code from employees.csv must be mapped through location_aliases to get canonical_location_id. Some employees (regional) may not have a location.

7. **Task/exception separation**: One exception can have multiple tasks. Tasks are reassignable without creating new exceptions. Exception resolution requires all tasks to be verified.

8. **State machine enforcement**: Server validates all state transitions. Client cannot set arbitrary states.

9. **No supplier fault**: Findings are neutral - they report receipt shortfall against PO, not supplier fault. The data doesn't include ASN/dispatch info.

10. **AI boundary**: AI can explain findings and suggest investigation steps, but cannot do arithmetic, decide eligibility, or mark outcomes verified. All AI output is validated.

11. **Regional employees**: HR-501 (regional_operations) has no store location and is not eligible for store-specific receiving tasks. This is intentional - not all employees have store assignments.

12. **WMS-MAT-2002 mapping**: This maps to SKU-002 with EA→EA factor 1, while ERP-BOX-12 maps to SKU-002 with BOX→EA factor 12. Both are valid mappings to the same canonical product, just from different source systems with different UOMs.

---

## Phase 2: Implementation Plan

### Project Structure
```
opsib-backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application entry point
│   ├── config.py               # Configuration and settings
│   ├── api/
│   │   ├── __init__.py
│   │   ├── imports.py          # Import endpoints
│   │   ├── reconciliation.py   # Reconciliation endpoints
│   │   ├── exceptions.py       # Exception endpoints
│   │   ├── tasks.py            # Task endpoints
│   │   └── models.py           # API request/response models
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py          # Database session management
│   │   └── base.py             # SQLAlchemy base
│   ├── models/
│   │   ├── __init__.py
│   │   ├── product.py
│   │   ├── location.py
│   │   ├── import_.py
│   │   ├── po_line.py
│   │   ├── receipt_line.py
│   │   ├── employee.py
│   │   ├── workforce_status.py
│   │   ├── exception.py
│   │   └── task.py
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── loader.py           # Generic CSV loader
│   │   ├── validators.py       # Row validators
│   │   └── handlers/
│   │       ├── __init__.py
│   │       ├── product_aliases.py
│   │       ├── location_aliases.py
│   │       ├── employees.py
│   │       ├── workforce_status.py
│   │       ├── po_lines.py
│   │       └── receipt_lines.py
│   ├── mapping/
│   │   ├── __init__.py
│   │   └── resolver.py         # Product/location mapping resolution
│   ├── detection/
│   │   ├── __init__.py
│   │   └── shortfall_detector.py
│   ├── routing/
│   │   ├── __init__.py
│   │   └── employee_router.py
│   ├── workflows/
│   │   ├── __init__.py
│   │   ├── state_machine.py    # Task state machine
│   │   └── audit.py            # Audit event recording
│   └── ai/
│       ├── __init__.py
│       └── provider.py         # Optional AI provider interface
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 001_initial_schema.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_uom_conversion.py
│   │   ├── test_shortfall_detection.py
│   │   ├── test_state_machine.py
│   │   └── test_employee_routing.py
│   └── integration/
│       ├── test_imports.py
│       ├── test_reconciliation.py
│       ├── test_idempotency.py
│       └── test_workflow.py
├── migrations/
│   └── (alembic auto-generates here)
├── fixtures/
│   └── (copy of fixture ZIP contents for testing)
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── pyproject.toml
├── ruff.toml
├── .env.example
└── README.md
```

### Implementation Phases

**Phase 1 (Now)**: Bootstrap
- Project structure
- Configuration
- Database connection
- Application entry point
- Initial migration
- Health endpoint
- Test configuration

**Phase 2**: Data Models & Migrations
- All SQLAlchemy models
- Alembic migration
- Database constraints

**Phase 3**: Ingestion
- CSV loaders
- Validators
- Import handlers for each dataset
- Idempotency logic
- Import endpoints

**Phase 4**: Mapping & Reconciliation
- Product/location mapping resolver
- UOM conversion
- Shortfall detector
- Reconciliation endpoint

**Phase 5**: Employee Routing
- Employee router
- Eligibility logic
- Fallback logic

**Phase 6**: Task Lifecycle
- State machine
- Task endpoints
- Audit events

**Phase 7**: Testing
- Unit tests
- Integration tests
- Idempotency tests
- Failure path tests

**Phase 8**: Documentation
- README
- API documentation
- Architecture notes
- Assumptions and limitations

Let me start implementing Phase 1 now.
