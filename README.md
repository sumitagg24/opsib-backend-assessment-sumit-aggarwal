# OPSiB Backend - Retail Data Integration & Exception Detection

A FastAPI-based backend service for retail operations that integrates ERP, WMS, and HRMS data to detect receipt shortfalls, route tasks to eligible employees, and maintain an auditable workflow.

## Assessment Context

This is the implementation for the OPSiB Backend Engineering Assessment. See [OPSiB_ANALYSIS.md](OPSiB_ANALYSIS.md) for detailed analysis of requirements, data mappings, and expected outcomes. The executed verification results are recorded in [VERIFICATION_REPORT.md](VERIFICATION_REPORT.md).

## Architecture

```
opsib-backend/
├── app/
│   ├── main.py              # FastAPI application entry point
│   ├── config.py            # Configuration (frozen time, DB URL, etc.)
│   ├── api/                 # API endpoints
│   │   ├── imports.py       # Import endpoints
│   │   ├── reconciliation.py # Reconciliation endpoints
│   │   ├── exceptions.py    # Exception endpoints
│   │   ├── tasks.py         # Task workflow endpoints
│   │   └── models.py        # Request/response schemas
│   ├── db/                  # Database layer
│   │   ├── base.py          # SQLAlchemy base
│   │   └── session.py       # Session management
│   ├── models/              # SQLAlchemy models
│   │   ├── product.py       # Product entity
│   │   ├── location.py      # Location entity
│   │   ├── import_.py       # Import batch & errors
│   │   ├── po_line.py       # Purchase order lines
│   │   ├── receipt_line.py  # Goods receipt lines
│   │   ├── employee.py      # Employee entity
│   │   ├── workforce_status.py # Shift/availability
│   │   ├── exception.py     # Exception findings
│   │   └── task.py          # Task & audit events
│   ├── ingestion/           # CSV ingestion
│   │   ├── loader.py        # Generic CSV loader
│   │   └── handlers/        # Dataset-specific handlers
│   ├── mapping/             # ID resolution
│   │   └── resolver.py      # Product/location mapping
│   ├── detection/           # Exception detection
│   │   └── shortfall_detector.py
│   ├── routing/             # Employee routing
│   │   └── employee_router.py
│   └── workflows/           # Task lifecycle
│       └── state_machine.py # Server-enforced state machine
├── alembic/                 # Database migrations
├── tests/                   # Test suite
├── fixtures/                # Assessment fixture CSVs
├── docker-compose.yml       # PostgreSQL for development
├── requirements.txt         # Python dependencies
└── README.md               # This file
```

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL 16 (the supplied `docker-compose.yml` starts one)
- Docker Compose (optional if PostgreSQL is already available)
- Git

### Setup

1. **Clone and navigate:**
   ```bash
   cd opsib-backend
   ```

2. **Create virtual environment and install dependencies:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Unix/Mac
   # .venv\Scripts\activate         # Windows
   python -m pip install -r requirements.txt
   ```

3. **Start PostgreSQL (Docker Compose):**
   ```bash
   docker compose up -d db
   ```

4. **Run database migrations:**
   ```bash
   export DATABASE_URL=postgresql://opsib:opsib@localhost:5432/opsib
   python -m alembic upgrade head
   ```

5. **Run the application:**
   ```bash
   python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

6. **Open API docs:**
   - Swagger: http://localhost:8000/docs
   - ReDoc: http://localhost:8000/redoc
   - Health: http://localhost:8000/health

The compose file exposes PostgreSQL on host port 5432 by default. Set
`POSTGRES_HOST_PORT=5433` (and the matching `DATABASE_URL`) if 5432 is already in use.
`docker compose --profile app up -d` also builds and runs the API container; the
plain `docker compose up -d db` workflow above keeps migrations and the API on the host.

### Environment Variables

Create a `.env` file based on `.env.example`:

```bash
# Application
APP_HOST=0.0.0.0
APP_PORT=8000
APP_DEBUG=false

# Frozen evaluation time (DO NOT CHANGE - assessment requirement)
FROZEN_EVALUATION_TIME=2026-10-16T15:00:00+05:30

# Database
DATABASE_URL=postgresql://opsib:opsib@localhost:5432/opsib

# Shift observation freshness window (hours)
SHIFT_FRESHNESS_HOURS=2

# Reconciliation tolerance (zero = strict)
RECONCILIATION_TOLERANCE_EA=0

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

## API Endpoints

### Health Check

```bash
curl http://localhost:8000/health
```

Response:
```json
{
  "status": "healthy",
  "database": "connected",
  "frozen_evaluation_time": "2026-10-16T15:00:00+05:30",
  "service": "OPSiB Backend",
  "version": "0.1.0"
}
```

### Import Datasets

Import the fixture CSVs to populate the database:

```bash
# Import product aliases first (needed for other imports)
curl -X POST http://localhost:8000/api/v1/imports/product_aliases \
  -F "file=@fixtures/product_aliases.csv" \
  -F "import_reference=fixture-load-001"

# Import location aliases
curl -X POST http://localhost:8000/api/v1/imports/location_aliases \
  -F "file=@fixtures/location_aliases.csv" \
  -F "import_reference=fixture-load-001"

# Import employees
curl -X POST http://localhost:8000/api/v1/imports/employees \
  -F "file=@fixtures/employees.csv" \
  -F "import_reference=fixture-load-001"

# Import workforce status
curl -X POST http://localhost:8000/api/v1/imports/workforce_status \
  -F "file=@fixtures/workforce_status.csv" \
  -F "import_reference=fixture-load-001"

# Import purchase order lines
curl -X POST http://localhost:8000/api/v1/imports/purchase_order_lines \
  -F "file=@fixtures/purchase_order_lines.csv" \
  -F "import_reference=fixture-load-001"

# Import goods receipt lines
curl -X POST http://localhost:8000/api/v1/imports/goods_receipt_lines \
  -F "file=@fixtures/goods_receipt_lines.csv" \
  -F "import_reference=fixture-load-001"
```

### Run Reconciliation

```bash
curl -X POST "http://localhost:8000/api/v1/reconciliation/run"
```

Response includes detected exceptions with evidence links.

### Data Quality

```bash
curl http://localhost:8000/api/v1/data-quality
```

The response lists unresolved product/location mappings, rejected rows and
conflicting source records. `GET /api/v1/imports/data-quality` is an equivalent
alias.

### List Exceptions

```bash
curl http://localhost:8000/api/v1/exceptions
curl "http://localhost:8000/api/v1/exceptions?status=OPEN"
```

### List Tasks

```bash
curl http://localhost:8000/api/v1/tasks
curl "http://localhost:8000/api/v1/tasks?assignee_id=HR-102"
curl "http://localhost:8000/api/v1/tasks?status=ASSIGNED"
```

### Task Workflow

```bash
# Acknowledge a task
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/acknowledge

# Start a task
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/start

# Complete a task
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/complete \
  -H "Content-Type: application/json" \
  -d '{"resolution_code": "DONE", "evidence_reference": "GRN-501-A checked"}'

# Reassign a task (new assignee must pass the same eligibility policy)
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/reassign \
  -H "Content-Type: application/json" \
  -d '{"assignee_employee_id": "HR-401", "reason": "Original assignee off-shift", "actor_employee_id": "HR-201"}'

# Verify a task and, once all its tasks are verified, resolve the exception
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/verify
curl -X POST http://localhost:8000/api/v1/tasks/exceptions/{exception_id}/verify \
  -H "Content-Type: application/json" \
  -d '{"resolution_code": "SHORTFALL_CONFIRMED", "evidence_reference": "INV-501"}'
```

## Data Model

### Key Entities

- **Product**: Canonical product with base UOM
- **Location**: Canonical location with region
- **POLine**: Purchase order line (ERP source)
- **ReceiptLine**: Goods receipt line (WMS source)
- **Employee**: HRMS employee with role and status
- **WorkforceStatus**: Shift/availability snapshot
- **Exception**: Business finding (e.g., shortfall)
- **Task**: Work item for an employee
- **TaskAuditEvent**: Audit trail for task changes

### Canonical Mapping

Source system IDs are mapped to canonical IDs:

| Source System | Source ID | Canonical |
|--------------|-----------|-----------|
| ERP | ERP-SKU-001 | SKU-001 |
| WMS | WMS-BRC-001 | SKU-001 |
| ERP | ERP-BOX-12 | SKU-002 (BOX→EA, factor 12) |
| WMS | WMS-MAT-2002 | SKU-002 (EA→EA, factor 1) |

| Source System | Source Location | Canonical Location |
|--------------|----------------|-------------------|
| ERP | MUM-014 | STORE-014 |
| WMS | WH-MH-014 | STORE-014 |
| HRMS | ST-014 | STORE-014 |

## UOM Conversion

Quantities are converted to canonical base UOM at ingestion time:

- **BOX → EA**: Multiply by factor (e.g., 10 BOX × 12 = 120 EA)
- **EA → EA**: No conversion needed (factor 1)

Conversion uses the `to_base_factor` from product_aliases.csv.

## Idempotency

- **Identical duplicates**: Ignored on re-import (same source_record_id, same content)
- **Conflicting duplicates**: Rejected and flagged for review (same source_record_id, different content)
- **Task completion retries**: Safe/idempotent - if already completed, returns current state

Database uniqueness constraints prevent duplicate facts:
- `uq_po_lines_source`: (source_system, source_record_id)
- `uq_receipt_lines_source`: (source_system, source_record_id)
- `uq_exceptions_type_key`: (exception_type, business_key)
- `uq_tasks_exception_type`: (exception_id, task_type)

## Employee Routing Rules

1. **Primary**: receiving_associate at the same location, ACTIVE, ON_SHIFT with a fresh observation
2. **Fallback**: store_manager at the same location, ACTIVE, ON_SHIFT with a fresh observation
3. **Inactive employees**: Never assigned (HR-103 is INACTIVE despite ON_SHIFT)
4. **Off-shift employees**: Not assigned (HR-101 is OFF_SHIFT)
5. **Regional employees**: May not have a store location; region-scoped roles are not assigned store tasks (HR-501)
6. **Fallback manager**: HR-401 for STORE-015 (no receiving associate available)
7. **Manual reassignment**: Enforces the same location, role, ACTIVE and ON_SHIFT policy
   as automatic routing, so a reassignment cannot bypass eligibility.

## Frozen Evaluation Time

All deadline comparisons use the frozen time from the fixture README:
**2026-10-16T15:00:00+05:30 (Asia/Kolkata)**

It is the default for every run. `POST /api/v1/reconciliation/run` also accepts an
optional `evaluation_time` query parameter for tests and re-runs; the assessment
scenarios use the frozen default. The machine clock is never used for scenario logic
(only for audit timestamps).

## Required Scenarios (Verified)

| PO | Expected Outcome | Calculation |
|----|-----------------|-------------|
| PO-501 | 8 EA shortfall | 100 ordered - (60 + 32) received = 8 |
| PO-502 | No shortfall | 40 ordered - 40 received = 0 |
| PO-503 | No actionable shortfall | Deadline 2026-10-17 is in the future |
| PO-504 | No shortfall | PO is CANCELLED |
| PO-505 | 24 EA shortfall | (10 BOX × 12) - 96 EA = 120 - 96 = 24 |
| PO-506 | 10 EA shortfall | 20 ordered - 10 received = 10 (duplicate counted once) |

## Testing

PostgreSQL must be reachable. The suite resets the `public` schema of the target
database, so point `TEST_DATABASE_URL` at a disposable test database (the fixture
creates it if the role is allowed to; otherwise run `createdb opsib_test` first).

```bash
export TEST_DATABASE_URL=postgresql://opsib:opsib@localhost:5432/opsib_test

# All tests (61, including 32 real-PostgreSQL end-to-end tests)
python -m pytest -q

# Real PostgreSQL end-to-end suite (32 tests, uses the Alembic migration)
python -m pytest tests/integration/test_postgres_end_to_end.py -v

# Other categories
python -m pytest tests/unit/ -v
python -m pytest tests/integration/ -v
python -m pytest tests/ -k "idempotency" -v
python -m pytest tests/ -k "workflow" -v
```

Expected test results:
- All 6 PO scenarios verified against real PostgreSQL
- Idempotency: importing twice produces the same canonical rows and no new exceptions
- Employee routing: HR-102 selected for STORE-014, HR-401 for STORE-015; HR-101/HR-103 never assigned
- State machine: invalid transitions rejected, valid transitions allowed
- Audit history: every state transition and reassignment recorded
- Failure paths: unknown mapping, conflicting duplicate, absent eligible assignee, unverified exception

Formatting and linting (Ruff is pinned in `requirements.txt`):

```bash
python -m ruff check .
python -m ruff format --check .
```

## Running with Docker

```bash
# Build image
docker build -t opsib-backend .

# Run database and API (app profile)
docker compose --profile app up -d

# View logs
docker compose logs -f app
```

## Production Considerations

This implementation includes:

- Database transactions for atomic operations
- Uniqueness constraints for idempotency
- Server-enforced state machine
- Audit trail for all changes
- Structured logging
- Timezone-aware datetimes

Not yet implemented (would require additional work):

- Tenant-level authorization
- Secure secret storage (use environment variables or secrets manager)
- Monitoring and alerting
- Rate limiting
- Request validation beyond Pydantic
- AI provider integration (optional in the brief; the deterministic boundary is documented but no live model call is wired)

## AI Tooling Disclosure

This implementation was created with AI assistance. The following parts were independently reviewed:

- Data model design and relationships
- Business logic for shortfall detection
- Employee routing rules and eligibility
- State machine transitions and validation
- Test cases for all 6 required scenarios

AI was used for:
- Initial code structure and boilerplate
- SQLAlchemy model definitions
- FastAPI endpoint scaffolding
- Documentation structure

All business logic was reviewed against the assessment requirements and fixture data.

## Limitations

1. **No supplier dispatch/ASN data**: Findings are neutral - they report receipt shortfall, not supplier fault
2. **Single tenant**: No multi-tenant isolation in this implementation
3. **Evaluation time override**: Intended for tests/re-runs; production scenario logic always uses the frozen default
4. **No real auth**: API is open for assessment purposes; actor IDs are supplied explicitly on reassignment
5. **CSV-only ingestion**: No support for other formats in this version
6. **No live AI call**: The optional AI boundary is a documented design, not an external dependency

## License

Confidential - OPSiB Backend Engineering Assessment
