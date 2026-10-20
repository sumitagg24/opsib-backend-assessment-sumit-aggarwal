# OPSiB Backend Assessment — Verification Report

**Date:** 2026-10-20
**Implementation:** `opsib-backend/`
**Verdict:** **READY TO SUBMIT**
**Verified against:** real PostgreSQL 16.15, Python 3.12, the supplied fixture CSVs, the HTTP API, the Alembic migration, and the task workflow.

This report separates what was executed from what was inspected or not run. It does not treat mocked tests as database verification.

---

## 1. Environment

| Item | Value |
|---|---|
| Runtime | Python 3.12 |
| Database | PostgreSQL 16.15 (isolated local cluster, `127.0.0.1:55432`) |
| Raw PG URL | `postgresql://opsib@127.0.0.1:55432/opsib` |
| Test DB | `postgresql://opsib@127.0.0.1:55432/opsib_test` |
| Migration | Alembic revision `001` (head) |
| Fixtures | Six supplied CSVs, byte-identical to the assessment pack |
| Container runtime | **Not available** in this environment (Docker Desktop WSL integration disabled) |

The database is a real PostgreSQL server, not SQLite and not an in-memory substitute.

---

## 2. Commands and exit codes

| # | Command | Exit | Observed result |
|---|---|---|---|
| 1 | `python -m alembic upgrade head` against an empty database | 0 | `Running upgrade -> 001, Initial schema for OPSiB backend` |
| 2 | `python -m pytest tests/integration/test_postgres_end_to_end.py -q` | 0 | `32 passed` |
| 3 | `python -m pytest -q` | 0 | `61 passed` |
| 4 | `ruff check .` (0.15.12 and pinned 0.1.11) | 0 | `All checks passed!` |
| 5 | `ruff format --check .` (0.15.12 and pinned 0.1.11) | 0 | `53 files already formatted` |
| 6 | `GET /health` | 200 | `{"status":"healthy","database":"connected",...}` |

Exact test command:

```bash
cd opsib-backend
TEST_DATABASE_URL=postgresql://opsib@127.0.0.1:55432/opsib_test \
PYTHONPATH=/tmp/opencode/opsib-deps:. \
python3 -m pytest -q
```

Test breakdown:

| Suite | Tests | Result |
|---|---:|---|
| `tests/unit/test_shortfall_detection.py` | 11 | passed |
| `tests/integration/test_employee_routing.py` | 5 | passed |
| `tests/integration/test_idempotency.py` | 3 | passed |
| `tests/integration/test_workflow.py` | 10 | passed |
| `tests/integration/test_postgres_end_to_end.py` | 32 | passed |
| **Total** | **61** | **passed** |

`test_postgres_end_to_end.py` is the authoritative real-PostgreSQL suite. It resets the test schema and applies the real Alembic migration; it does not use `create_all`.

---

## 3. Fresh-database migration

The live `opsib` database was dropped and recreated, then migrated from empty:

- `dropdb --force opsib` → exit 0
- `createdb opsib` → exit 0
- `alembic upgrade head` → exit 0

Post-migration checks:

- 23 timestamp columns are `timestamp with time zone`; `timestamp without time zone` count = **0**.
- UUID primary keys are native PostgreSQL `uuid`.
- `exceptions.po_line_number` is an integer.

---

## 4. Live HTTP verification

All calls below were made against the running FastAPI application backed by the freshly migrated database.

### 4.1 Imports

| Dataset | HTTP | Accepted | Rejected | Duplicates | Conflicts |
|---|---:|---:|---:|---:|---:|
| `product_aliases` | 200 | 6 | 0 | 0 | 0 |
| `location_aliases` | 200 | 6 | 0 | 0 | 0 |
| `employees` | 200 | 6 | 0 | 0 | 0 |
| `workforce_status` | 200 | 6 | 0 | 0 | 0 |
| `purchase_order_lines` | 200 | 6 | 0 | 1 | 0 |
| `goods_receipt_lines` | 200 | 6 | 0 | 1 | 0 |

Observed database row counts after import: 6 products/aliases, 6 locations/aliases,
6 employees, 6 workforce observations, 6 PO lines, 6 receipt lines.

`GET /api/v1/data-quality` → 200 with empty unresolved/rejected/conflicting lists.
`GET /api/v1/imports/{import_id}` → 200 with row-level counts and `errors: []`.
`GET /api/v1/imports/not-a-uuid` → 400 `Invalid import ID format`.

### 4.2 Reconciliation

`POST /api/v1/reconciliation/run` → 200

| Metric | Observed |
|---|---|
| PO lines evaluated | 4 |
| Exceptions created | 3 |
| PO-501 | 8 EA shortfall (100 − 92) |
| PO-505 | 24 EA shortfall (120 − 96) |
| PO-506 | 10 EA shortfall (20 − 10, duplicate receipt counted once) |
| PO-502 | no exception (fully received) |
| PO-503 | no exception (deadline in the future) |
| PO-504 | no exception (cancelled) |

The three exceptions contained the expected PO/line, canonical product/location,
expected/received/variance quantities, base UOM, rule/mapping versions, and
linked source-record evidence.

### 4.3 Task assignment

| Exception | Location | Assigned to | Reason |
|---|---|---|---|
| PO-501 | STORE-014 | HR-102 | eligible receiving_associate |
| PO-505 | STORE-015 | HR-401 | no receiver; store_manager fallback |
| PO-506 | STORE-014 | HR-102 | eligible receiving_associate |

HR-101 (OFF_SHIFT) and HR-103 (INACTIVE) were not assigned.

### 4.4 Idempotency

The same six CSVs and reconciliation were run a second time:

- Import responses: `0 accepted`, `6/7 duplicates`, `0 conflicts` per dataset.
- Domain-table counts unchanged before/after: products, locations, employees,
  workforce rows, PO lines, receipt lines, exceptions, tasks, evidence, audit events.
- Reconciliation: `evaluated=4`, `exceptions_created=0`, `returned=3`.
- Re-import created new import-batch audit rows only; it did not duplicate canonical facts.

### 4.5 Task lifecycle (PO-501)

| Operation | HTTP | State/result |
|---|---:|---|
| acknowledge | 200 | `ACKNOWLEDGED` |
| start | 200 | `IN_PROGRESS` |
| complete (`INVESTIGATED`, `INV-501`) | 200 | `COMPLETED` |
| pending verification | 200 | `PENDING_VERIFICATION` |
| complete retry while pending | 200 | `PENDING_VERIFICATION`, `Task was already completed (idempotent retry)` |
| verify | 200 | `VERIFIED` |
| complete retry after verify | 400 | `Cannot complete a VERIFIED task...` |
| reassign after verify | 400 | `Cannot reassign a task in status VERIFIED...` |
| exception verify | 200 | `RESOLVED`, resolution `SHORTFALL_CONFIRMED`, `resolved_at` set |
| repeated exception verify | 200 | `Exception is already resolved`, timestamp unchanged |

Audit history for the task recorded `ASSIGNED`, `ACKNOWLEDGED`, `STARTED`,
`COMPLETED`, `COMPLETION_RECORDED`, `COMPLETION_RETRY`, and `VERIFIED`.

### 4.6 Reassignment guards (PO-505, STORE-015)

| Target | HTTP | Observed detail |
|---|---:|---|
| HR-103 (INACTIVE) | 400 | `Employee HR-103 is INACTIVE, not ACTIVE` |
| HR-101 (OFF_SHIFT) | 400 | not eligible for assignment at STORE-015 |
| HR-501 (region-only role) | 400 | not eligible for assignment at STORE-015 |
| HR-102 (different store) | 400 | not eligible for assignment at STORE-015 |
| HR-401 with unknown actor HR-999 | 400 | `Actor employee HR-999 not found` |
| HR-201 at same store (PO-506) | 200 | assignee changed; actor and reason recorded |

After all rejected attempts, the PO-505 task still belonged to HR-401.

---

## 5. Defects found during verification and fixed

1. **ORM/migration drift — UUID keys:** primary keys declared as `String(36)` while the migration created native `uuid`; changed to SQLAlchemy `Uuid`.
2. **ORM/migration drift — line number:** `exceptions.po_line_number` was a `DateTime`; changed to `Integer`.
3. **Reserved column name:** `TaskAuditEvent.metadata` mapped correctly as JSON to the existing `metadata` column.
4. **Detector eligibility:** now filters OPEN lines, deadlines at/before the evaluation time, posted receipts received at/before evaluation, duplicate source receipts keyed by `(source_system, source_record_id)`, and eligible batch lines.
5. **Reconciliation reporting:** returns real evaluated/created counts instead of input lengths; routing failures are persisted instead of being swallowed.
6. **Route ordering:** `/imports/data-quality` is declared before `/imports/{import_id}` so it is not captured as a UUID.
7. **Concurrency:** task mutations lock the task row and commit atomically.
8. **Completion retries:** idempotent in `PENDING_VERIFICATION`; rejected once `VERIFIED`.
9. **Reassignment policy bypass:** reassignment previously checked only `ACTIVE`, so HR-101 (OFF_SHIFT) and HR-501 (region-only) could be assigned. Reassignment now enforces the same ACTIVE, fresh ON_SHIFT, location, and approved-role policy as automatic routing.
10. **Frozen timestamps:** model defaults used `datetime.datetime.now(...)`, which was evaluated once at import time and gave every row the process start time. Defaults now use the `utc_now` callable, verified per row and by regression test.
11. **Startup import:** `app/main.py` used `logging.config` without importing it.
12. **API robustness:** invalid import/batch UUIDs return 400, and import responses expose the `errors` list.
13. **Dependency pin:** `pytest-asyncio` pinned below 0.24 for the test harness.

Regression coverage was added for schema parity (types, nullability, unique
constraints, native UUID, integer line number, callable timestamp defaults),
reconciliation counters and filters, late receipts, cross-system receipt dedupe, conflicting duplicates,
unknown mappings, no-eligible-assignee routing, HTTP import/data-quality routes,
reassignment actor/status/eligibility guards, and completion retries.

---

## 6. Docker Compose / Dockerfile

Static inspection only:

- `docker-compose.yml` defines PostgreSQL 16-alpine with a healthcheck, named
  volume, configurable host port, and an optional `app` profile.
- `Dockerfile` installs `requirements.txt`, copies the project, exposes port 8000,
  has a healthcheck, and runs `alembic upgrade head` before Uvicorn.
- `tests/integration/test_postgres_end_to_end.py` and the compose/Docker files are
  included in the rebuilt archive.

**Not executed:** `docker compose up`, `docker build`. Docker Desktop's WSL
integration is disabled in this environment, so the container path could not be
runtime-tested. Database behavior was verified against the local real PostgreSQL
16.15 cluster instead. This is the only verification gap.

---

## 7. Known limitations

- Single tenant; no authentication or tenant-level authorization.
- Actor identity on acknowledge/start/complete is recorded as SYSTEM because the
  assessment API has no authenticated principal; reassignment accepts and records
  an explicit actor.
- No live Claude/AI call. The deterministic detector remains authoritative; AI is
  an optional, documented boundary.
- CSV ingestion only.
- Docker/Compose runtime not exercised here (see section 6).
