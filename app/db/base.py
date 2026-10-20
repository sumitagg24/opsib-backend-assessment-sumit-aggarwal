"""SQLAlchemy base setup."""

import datetime

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass  # Individual models define their own timestamps


def utc_now() -> datetime.datetime:
    """Return current UTC time."""
    return datetime.datetime.now(datetime.UTC)
