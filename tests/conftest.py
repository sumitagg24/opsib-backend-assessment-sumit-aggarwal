"""Pytest configuration and fixtures."""

import os
import sys
from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# The test database must be selected BEFORE app.config is imported: app.config
# builds a module-level settings singleton at import time, and app.db.session
# reads that singleton. Setting DATABASE_URL later (inside a fixture) would be
# silently ignored and tests would run against the real database instead.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://opsib:opsib@localhost:5432/opsib_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["FROZEN_EVALUATION_TIME"] = "2026-10-16T15:00:00+05:30"
os.environ["SHIFT_FRESHNESS_HOURS"] = "2"
os.environ["RECONCILIATION_TOLERANCE_EA"] = "0"

from app.config import Settings, get_settings  # noqa: E402


def _test_database_problem() -> str | None:
    """Check PostgreSQL reachability, creating the test database when missing.

    Returns None when the test database is usable, otherwise a short reason
    that tests can surface through ``pytest.skip``.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    url = make_url(TEST_DATABASE_URL)
    first_error = None

    try:
        engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return None
    except Exception as exc:  # noqa: BLE001 - any failure means "not usable"
        first_error = str(exc).splitlines()[0]

    # The database itself may simply not exist yet (fresh checkout / fresh
    # container): create it and retry once.
    try:
        admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
        admin.dispose()
        engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return None
    except Exception as exc:  # noqa: BLE001 - report why creation failed too
        return f"{first_error} (create attempt: {str(exc).splitlines()[0]})"


@pytest.fixture(scope="session")
def postgres_problem() -> str | None:
    """None when PostgreSQL is reachable for tests, else the failure reason."""
    return _test_database_problem()


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Application settings already bound to the test database."""
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture(scope="session")
def engine(settings, postgres_problem) -> Generator:
    """Create test database engine (skips when PostgreSQL is unavailable)."""
    from sqlalchemy import create_engine

    if postgres_problem:
        pytest.skip(f"PostgreSQL unavailable: {postgres_problem}")

    test_engine = create_engine(settings.database_url)
    yield test_engine
    test_engine.dispose()


@pytest.fixture(scope="session")
def tables(engine) -> Generator:
    """Build the test schema with the same Alembic migration used in production.

    Using ``Base.metadata.create_all`` here would hide drift between the ORM
    models and the migration (the exact class of bug this suite exists to
    catch), so the test database is migrated from an empty schema instead.
    """
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)

    # The fixture owns a disposable test database: reset it completely so a
    # previous interrupted run cannot leave half-migrated state behind.
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))

    command.upgrade(config, "head")

    yield

    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def db_session(tables) -> Generator:
    """Create a test database session."""
    from sqlalchemy import delete

    from app.db.base import Base
    from app.db.session import get_session_factory

    factory = get_session_factory()
    session = factory()

    # Start every test from an empty schema: the schema itself is created once
    # per session, so rows must not leak between tests.
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(delete(table))
    session.commit()

    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def frozen_time() -> datetime:
    """Return the frozen evaluation time as a datetime object."""
    from dateutil.parser import parse

    return parse("2026-10-16T15:00:00+05:30")


@pytest.fixture
def sample_product_data():
    """Sample product alias data for testing."""
    return [
        {
            "source_system": "ERP",
            "external_item_id": "ERP-SKU-001",
            "canonical_product_id": "SKU-001",
            "source_uom": "EA",
            "base_uom": "EA",
            "to_base_factor": Decimal("1"),
        },
        {
            "source_system": "WMS",
            "external_item_id": "WMS-BRC-001",
            "canonical_product_id": "SKU-001",
            "source_uom": "EA",
            "base_uom": "EA",
            "to_base_factor": Decimal("1"),
        },
        {
            "source_system": "ERP",
            "external_item_id": "ERP-BOX-12",
            "canonical_product_id": "SKU-002",
            "source_uom": "BOX",
            "base_uom": "EA",
            "to_base_factor": Decimal("12"),
        },
        {
            "source_system": "WMS",
            "external_item_id": "WMS-MAT-2002",
            "canonical_product_id": "SKU-002",
            "source_uom": "EA",
            "base_uom": "EA",
            "to_base_factor": Decimal("1"),
        },
    ]


@pytest.fixture
def sample_location_data():
    """Sample location alias data for testing."""
    return [
        {
            "source_system": "ERP",
            "external_location_id": "MUM-014",
            "canonical_location_id": "STORE-014",
            "region_id": "REGION-MH",
        },
        {
            "source_system": "WMS",
            "external_location_id": "WH-MH-014",
            "canonical_location_id": "STORE-014",
            "region_id": "REGION-MH",
        },
        {
            "source_system": "HRMS",
            "external_location_id": "ST-014",
            "canonical_location_id": "STORE-014",
            "region_id": "REGION-MH",
        },
    ]
