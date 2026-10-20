"""OPSiB Backend Application."""

import logging
import logging.config
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url

from app.api import exceptions, imports, reconciliation, tasks
from app.config import settings
from app.db import init_db

# Configure structured logging
logging_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": (
                '{"timestamp":"%(asctime)s","level":"%(levelname)s",'
                '"logger":"%(name)s","message":"%(message)s"}'
            ),
        },
        "standard": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": settings.log_format if settings.log_format == "json" else "standard",
        },
    },
    "root": {
        "level": settings.log_level,
        "handlers": ["console"],
    },
}

logging.config.dictConfig(logging_config)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting OPSiB Backend...")
    logger.info(f"Frozen evaluation time: {settings.frozen_evaluation_time}")
    logger.info(
        f"Database URL: {make_url(settings.database_url).render_as_string(hide_password=True)}"
    )

    try:
        init_db()
        logger.info("Database connection initialized")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

    yield

    # Shutdown
    logger.info("Shutting down OPSiB Backend...")


# Create FastAPI application
app = FastAPI(
    title="OPSiB Backend",
    description="Retail data integration, exception detection and task reliability service",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Add CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(imports.router, prefix="/api/v1/imports", tags=["Imports"])
app.include_router(reconciliation.router, prefix="/api/v1/reconciliation", tags=["Reconciliation"])
app.include_router(exceptions.router, prefix="/api/v1/exceptions", tags=["Exceptions"])
app.include_router(tasks.router, prefix="/api/v1/tasks", tags=["Tasks"])


@app.get(
    "/api/v1/data-quality",
    response_model=imports.DataQualityResponse,
    tags=["Imports"],
    include_in_schema=True,
)
async def data_quality_report(limit: int = 100):
    """Assessment-spec alias for the import data-quality report."""
    return await imports.get_data_quality(limit=limit)


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, Any]:
    """Health check endpoint."""
    from app.db.session import get_engine_instance

    try:
        engine = get_engine_instance()
        # Test database connection
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
        logger.error(f"Health check database error: {e}")

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "frozen_evaluation_time": settings.frozen_evaluation_time,
        "service": "OPSiB Backend",
        "version": "0.1.0",
    }


@app.get("/", tags=["Root"])
async def root() -> dict[str, Any]:
    """Root endpoint with service information."""
    return {
        "service": "OPSiB Backend",
        "version": "0.1.0",
        "description": "Retail data integration, exception detection and task reliability",
        "docs": "/docs",
        "health": "/health",
    }


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unhandled exceptions."""
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": str(exc) if settings.app_debug else "An unexpected error occurred",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
    )
