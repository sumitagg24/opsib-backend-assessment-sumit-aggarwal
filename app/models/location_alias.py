"""Location alias model."""

import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.location import Location


class LocationAlias(Base):
    """Location alias mapping from source system to canonical location."""

    __tablename__ = "location_aliases"

    id: Mapped[UUID] = mapped_column("id", Uuid, primary_key=True, default=uuid4)
    source_system: Mapped[str] = mapped_column(String(50), nullable=False)
    external_location_id: Mapped[str] = mapped_column(String(100), nullable=False)
    canonical_location_id: Mapped[str] = mapped_column(
        ForeignKey("locations.canonical_location_id"), nullable=False
    )
    region_id: Mapped[str] = mapped_column(String(50), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # Relationships
    canonical_location: Mapped["Location"] = relationship("Location")

    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "external_location_id",
            "mapping_version",
            name="uq_location_aliases_source_external_version",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<LocationAlias({self.source_system}:{self.external_location_id} "
            f"-> {self.canonical_location_id})>"
        )
