"""Database session management."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


def get_engine():
    """Create database engine from settings."""
    return create_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def get_session_factory(engine=None):
    """Create session factory."""
    if engine is None:
        engine = get_engine()
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_session() -> Session:
    """Get a new database session."""
    factory = get_session_factory()
    return factory()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    Provide a transactional scope around a series of operations.

    Usage:
        with session_scope() as session:
            # perform operations
            session.commit()
        # if exception occurs, session is rolled back
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Module-level engine and session factory for reuse
_engine = None
_session_factory = None


def init_db():
    """Initialize database engine and session factory (call once at startup)."""
    global _engine, _session_factory
    _engine = get_engine()
    _session_factory = get_session_factory(_engine)


def get_engine_instance():
    """Get the initialized engine instance."""
    global _engine
    if _engine is None:
        init_db()
    return _engine


def get_session_factory_instance():
    """Get the initialized session factory."""
    global _session_factory
    if _session_factory is None:
        init_db()
    return _session_factory


def create_all_tables():
    """Create all tables (for development/testing only)."""
    from app.db.base import Base

    engine = get_engine_instance()
    Base.metadata.create_all(bind=engine)


def drop_all_tables():
    """Drop all tables (for testing only)."""
    from app.db.base import Base

    engine = get_engine_instance()
    Base.metadata.drop_all(bind=engine)
