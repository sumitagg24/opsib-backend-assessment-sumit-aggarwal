"""Import API endpoints - actual implementation."""

import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.db import session_scope
from app.models.import_ import ImportBatch, ImportError
from app.models.location import Location
from app.models.location_alias import LocationAlias
from app.models.po_line import POLine
from app.models.product import Product
from app.models.product_alias import ProductAlias
from app.models.receipt_line import ReceiptLine

router = APIRouter()


# Dataset handler mapping
DATASET_HANDLERS = {
    "product_aliases": "app.ingestion.handlers.product_aliases_handler:ProductAliasesHandler",
    "location_aliases": "app.ingestion.handlers.location_aliases_handler:LocationAliasesHandler",
    "employees": "app.ingestion.handlers.employees_handler:EmployeesHandler",
    "workforce_status": "app.ingestion.handlers.workforce_status_handler:WorkforceStatusHandler",
    "purchase_order_lines": "app.ingestion.handlers.po_lines_handler:POLinesHandler",
    "goods_receipt_lines": "app.ingestion.handlers.receipt_lines_handler:ReceiptLinesHandler",
}

VALID_DATASETS = list(DATASET_HANDLERS.keys())


class ImportStartResponse(BaseModel):
    import_id: str
    dataset: str
    status: str
    message: str


class ImportErrorResponse(BaseModel):
    id: str
    source_record_id: str | None = None
    row_number: int | None = None
    error_code: str
    error_message: str
    raw_data: dict | None = None


class ImportBatchResponse(BaseModel):
    id: str
    dataset_name: str
    import_reference: str
    status: str
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    conflicting_rows: int
    rule_version: str
    mapping_version: str
    started_at: str
    completed_at: str | None = None
    error_message: str | None = None
    errors: list[ImportErrorResponse] = []


class DataQualityResponse(BaseModel):
    unresolved_product_mappings: list[dict] = []
    unresolved_location_mappings: list[dict] = []
    rejected_records: list[ImportErrorResponse] = []
    conflicting_records: list[dict] = []


def get_handler_class(dataset: str):
    """Get handler class for a dataset."""
    import importlib

    module_path, class_name = DATASET_HANDLERS[dataset].rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


@router.post(
    "/{dataset}",
    response_model=ImportStartResponse,
    responses={400: {"description": "Invalid dataset or file"}},
)
async def import_dataset(
    dataset: str,
    file: UploadFile = File(...),
    import_reference: str | None = Query(None, alias="import_reference"),
):
    """
    Import a dataset from CSV file.

    Supported datasets:
    - product_aliases
    - location_aliases
    - employees
    - workforce_status
    - purchase_order_lines
    - goods_receipt_lines
    """
    if dataset not in VALID_DATASETS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid dataset '{dataset}'. Must be one of: {', '.join(VALID_DATASETS)}",
        )

    # Read file content
    content = await file.read()

    # Create import batch
    import_id = str(uuid.uuid4())
    import_ref = import_reference or f"import-{import_id[:8]}"

    with session_scope() as session:
        # Check if datasets are loaded in correct order
        if dataset == "purchase_order_lines":
            po_count = session.query(POLine).count()
            if po_count == 0:
                # Check if mappings exist
                product_count = session.query(Product).count()
                location_count = session.query(Location).count()
                if product_count == 0 or location_count == 0:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Cannot import PO lines before product and location mappings are loaded"
                        ),
                    )

        if dataset == "goods_receipt_lines":
            receipt_count = session.query(ReceiptLine).count()
            if receipt_count == 0:
                po_count = session.query(POLine).count()
                if po_count == 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot import receipt lines before PO lines are loaded",
                    )

        # Create import batch record
        import_batch = ImportBatch(
            id=uuid.UUID(import_id),
            dataset_name=dataset,
            import_reference=import_ref,
            status="PROCESSING",
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            duplicate_rows=0,
            conflicting_rows=0,
            rule_version="1.0",
            mapping_version="1.0",
        )
        session.add(import_batch)
        session.commit()

    # Process import in background (or synchronously for now)
    try:
        handler_class = get_handler_class(dataset)

        with session_scope() as session:
            # Re-fetch import batch
            import_batch = (
                session.query(ImportBatch).filter(ImportBatch.id == uuid.UUID(import_id)).first()
            )

            if not import_batch:
                raise HTTPException(status_code=404, detail="Import batch not found")

            # Create handler and process
            handler = handler_class(session, import_batch)
            result = handler.process(content)

            return ImportStartResponse(
                import_id=import_id,
                dataset=dataset,
                status=result["status"],
                message=(
                    f"Import completed: {result['accepted']} accepted, "
                    f"{result['rejected']} rejected, "
                    f"{result['duplicates']} duplicates, "
                    f"{result['conflicts']} conflicts"
                ),
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {e!s}") from e


@router.get("/data-quality", response_model=DataQualityResponse)
async def get_data_quality(
    limit: int = Query(100, description="Maximum number of records to return"),
):
    """
    Get data quality report showing:
    - Unresolved product mappings
    - Unresolved location mappings
    - Rejected records
    - Conflicting records
    """
    with session_scope() as session:
        # Get unresolved product mappings (product aliases without canonical products)
        unresolved_products = (
            session.query(ProductAlias)
            .outerjoin(Product, ProductAlias.canonical_product_id == Product.canonical_product_id)
            .filter(Product.canonical_product_id.is_(None))
            .limit(limit)
            .all()
        )

        # Get unresolved location mappings
        unresolved_locations = (
            session.query(LocationAlias)
            .outerjoin(
                Location, LocationAlias.canonical_location_id == Location.canonical_location_id
            )
            .filter(Location.canonical_location_id.is_(None))
            .limit(limit)
            .all()
        )

        # Get recent rejected records
        rejected_records = (
            session.query(ImportError)
            .filter(ImportError.error_code != "DUPLICATE")
            .order_by(ImportError.created_at.desc())
            .limit(limit)
            .all()
        )

        # Get conflicting records
        conflicting_records = (
            session.query(ImportError)
            .filter(
                ImportError.error_code.in_(
                    [
                        "CONFLICTING_PO_LINE",
                        "CONFLICTING_RECEIPT_LINE",
                        "CONFLICTING_MAPPING",
                        "CONFLICTING_EMPLOYEE",
                        "CONFLICTING_WORKFORCE_STATUS",
                    ]
                )
            )
            .order_by(ImportError.created_at.desc())
            .limit(limit)
            .all()
        )

        return DataQualityResponse(
            unresolved_product_mappings=[
                {
                    "source_system": up.source_system,
                    "external_item_id": up.external_item_id,
                    "canonical_product_id": up.canonical_product_id,
                }
                for up in unresolved_products
            ],
            unresolved_location_mappings=[
                {
                    "source_system": ul.source_system,
                    "external_location_id": ul.external_location_id,
                    "canonical_location_id": ul.canonical_location_id,
                }
                for ul in unresolved_locations
            ],
            rejected_records=[
                ImportErrorResponse(
                    id=str(e.id),
                    source_record_id=e.source_record_id,
                    row_number=e.row_number,
                    error_code=e.error_code,
                    error_message=e.error_message,
                    raw_data=e.raw_data,
                )
                for e in rejected_records
            ],
            conflicting_records=[
                {
                    "source_record_id": e.source_record_id,
                    "error_code": e.error_code,
                    "error_message": e.error_message,
                    "row_number": e.row_number,
                }
                for e in conflicting_records
            ],
        )


@router.get(
    "/{import_id}",
    response_model=ImportBatchResponse,
    responses={400: {"description": "Invalid import id"}, 404: {"description": "Import not found"}},
)
async def get_import(import_id: str):
    """Get import batch status and details."""
    try:
        import_uuid = uuid.UUID(import_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid import ID format") from None

    with session_scope() as session:
        import_batch = session.query(ImportBatch).filter(ImportBatch.id == import_uuid).first()

        if not import_batch:
            raise HTTPException(status_code=404, detail=f"Import {import_id} not found")

        errors = (
            session.query(ImportError)
            .filter(ImportError.import_batch_id == import_batch.id)
            .order_by(ImportError.row_number)
            .all()
        )

        return ImportBatchResponse(
            id=str(import_batch.id),
            dataset_name=import_batch.dataset_name,
            import_reference=import_batch.import_reference,
            status=import_batch.status,
            total_rows=import_batch.total_rows,
            accepted_rows=import_batch.accepted_rows,
            rejected_rows=import_batch.rejected_rows,
            duplicate_rows=import_batch.duplicate_rows,
            conflicting_rows=import_batch.conflicting_rows,
            rule_version=import_batch.rule_version,
            mapping_version=import_batch.mapping_version,
            started_at=import_batch.started_at.isoformat() if import_batch.started_at else None,
            completed_at=import_batch.completed_at.isoformat()
            if import_batch.completed_at
            else None,
            error_message=import_batch.error_message,
            errors=[
                ImportErrorResponse(
                    id=str(e.id),
                    source_record_id=e.source_record_id,
                    row_number=e.row_number,
                    error_code=e.error_code,
                    error_message=e.error_message,
                    raw_data=e.raw_data,
                )
                for e in errors
            ],
        )
