"""Application configuration."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")

    # Frozen evaluation time from fixture README
    # This is the single source of truth for deadline comparisons
    frozen_evaluation_time: str = Field(
        default="2026-10-16T15:00:00+05:30",
        alias="FROZEN_EVALUATION_TIME",
    )

    # Database
    database_url: str = Field(
        default="postgresql://opsib:opsib@localhost:5432/opsib", alias="DATABASE_URL"
    )

    # Shift observation freshness window in hours
    shift_freshness_hours: int = Field(default=2, alias="SHIFT_FRESHNESS_HOURS")

    # Reconciliation tolerance in base UOM units (default zero)
    reconciliation_tolerance: int = Field(default=0, alias="RECONCILIATION_TOLERANCE_EA")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="json", alias="LOG_FORMAT")

    @property
    def frozen_evaluation_datetime(self) -> str:
        """Return the frozen evaluation time as a string for use in comparisons."""
        return self.frozen_evaluation_time

    @field_validator("frozen_evaluation_time")
    @classmethod
    def validate_frozen_time(cls, v: str) -> str:
        """Validate that frozen time is a valid ISO8601 datetime."""
        from dateutil.parser import parse as parse_datetime

        try:
            parse_datetime(v)
        except Exception as e:
            raise ValueError(f"Invalid frozen evaluation time: {v}") from e
        return v


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Convenience imports
settings = get_settings()
