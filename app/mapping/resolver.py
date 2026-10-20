"""Product and location mapping resolution."""

from decimal import Decimal

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.location import Location
from app.models.location_alias import LocationAlias
from app.models.product import Product
from app.models.product_alias import ProductAlias


class MappingResolver:
    """Resolves source product and location IDs to canonical entities."""

    def __init__(self, session: Session, mapping_version: str = "1.0"):
        """
        Initialize the resolver.

        Args:
            session: Database session
            mapping_version: Version of mappings to use
        """
        self.session = session
        self.mapping_version = mapping_version

    def resolve_product(
        self,
        source_system: str,
        external_item_id: str,
        source_uom: str,
        quantity: Decimal | None = None,
    ) -> tuple[Product | None, Decimal | None, str | None, str | None]:
        """
        Resolve a product from source system to canonical product.

        Args:
            source_system: Source system name (ERP, WMS, etc.)
            external_item_id: Product ID in source system
            source_uom: Unit of measure in source system
            quantity: Optional quantity to convert to base UOM

        Returns:
            Tuple of (product, qty_in_base_uom, error_code, error_message)
            If successful: (product, converted_qty_or_None, None, None)
            If failed: (None, None, error_code, error_message)
        """
        # Find the product alias
        alias = (
            self.session.query(ProductAlias)
            .filter(
                and_(
                    ProductAlias.source_system == source_system,
                    ProductAlias.external_item_id == external_item_id,
                    ProductAlias.mapping_version == self.mapping_version,
                )
            )
            .first()
        )

        if not alias:
            return (
                None,
                None,
                "PRODUCT_NOT_MAPPED",
                f"No mapping found for {source_system}:{external_item_id}",
            )

        # Get the canonical product
        product = alias.canonical_product
        if not product:
            return (
                None,
                None,
                "PRODUCT_MAPPING_INVALID",
                "Mapping exists but canonical product not found for "
                f"{source_system}:{external_item_id}",
            )

        # Convert quantity to base UOM if provided
        qty_in_base = None
        if quantity is not None:
            try:
                factor = Decimal(str(alias.to_base_factor))
                qty_in_base = quantity * factor
            except Exception as e:
                return (
                    None,
                    None,
                    "UOM_CONVERSION_FAILED",
                    f"Failed to convert quantity: {e}",
                )

        return (product, qty_in_base, None, None)

    def resolve_location(
        self,
        source_system: str,
        external_location_id: str,
    ) -> tuple[Location | None, str | None, str | None]:
        """
        Resolve a location from source system to canonical location.

        Args:
            source_system: Source system name (ERP, WMS, HRMS)
            external_location_id: Location ID in source system

        Returns:
            Tuple of (location, error_code, error_message)
        """
        # Find the location alias
        alias = (
            self.session.query(LocationAlias)
            .filter(
                and_(
                    LocationAlias.source_system == source_system,
                    LocationAlias.external_location_id == external_location_id,
                    LocationAlias.mapping_version == self.mapping_version,
                )
            )
            .first()
        )

        if not alias:
            return (
                None,
                "LOCATION_NOT_MAPPED",
                f"No mapping found for {source_system}:{external_location_id}",
            )

        # Get the canonical location
        location = alias.canonical_location
        if not location:
            return (
                None,
                "LOCATION_MAPPING_INVALID",
                "Mapping exists but canonical location not found for "
                f"{source_system}:{external_location_id}",
            )

        return (location, None, None)

    def bulk_load_mappings(self) -> dict:
        """
        Load all mappings into memory for faster lookups.

        Returns:
            Dictionary with product and location mappings
        """
        # Load all product aliases
        product_aliases = (
            self.session.query(ProductAlias)
            .filter(ProductAlias.mapping_version == self.mapping_version)
            .all()
        )

        # Load all location aliases
        location_aliases = (
            self.session.query(LocationAlias)
            .filter(LocationAlias.mapping_version == self.mapping_version)
            .all()
        )

        # Build lookup dictionaries
        product_map = {}
        for alias in product_aliases:
            key = (alias.source_system, alias.external_item_id)
            product_map[key] = {
                "canonical_product_id": alias.canonical_product_id,
                "source_uom": alias.source_uom,
                "base_uom": alias.base_uom,
                "to_base_factor": float(alias.to_base_factor),
                "product": alias.canonical_product,
            }

        location_map = {}
        for alias in location_aliases:
            key = (alias.source_system, alias.external_location_id)
            location_map[key] = {
                "canonical_location_id": alias.canonical_location_id,
                "region_id": alias.region_id,
                "location": alias.canonical_location,
            }

        return {
            "products": product_map,
            "locations": location_map,
        }
