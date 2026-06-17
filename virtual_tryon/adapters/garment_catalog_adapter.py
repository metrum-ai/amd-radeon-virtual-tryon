# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""GarmentCatalogAdapter — unified interface for catalog, search, and logistics."""

from __future__ import annotations

from virtual_tryon.catalog.catalog_service import CatalogService
from virtual_tryon.catalog.search_service import SearchService
from virtual_tryon.storage.postgres import PostgresPool
from virtual_tryon.storage.schemas import (
    BranchAvailability,
    CatalogQuery,
    GarmentItem,
    GarmentLogistics,
    GarmentSearchResult,
    SearchRequest,
)

_VALID_OVERLAY = {"tops", "bottoms", "one-pieces"}


class GarmentCatalogAdapter:
    """Adapter wrapping search, metadata, logistics, and history persistence.

    Args:
        catalog_service: CatalogService instance.
        search_service: SearchService instance.
        db: PostgresPool for logistics and history queries.

    """

    def __init__(
        self,
        catalog_service: CatalogService,
        search_service: SearchService,
        db: PostgresPool,
    ) -> None:
        """Initialize the GarmentCatalogAdapter."""
        self._catalog = catalog_service
        self._search = search_service
        self._db = db

    async def get_garment(self, item_id: str) -> GarmentItem | None:
        """Fetch a garment by ID.

        Args:
            item_id: UUID string.

        Returns:
            GarmentItem or None.

        Raises:
            ValueError: If found garment has invalid overlay_category.

        """
        item = await self._catalog.get_garment(item_id)
        if item is not None:
            _assert_overlay(item.overlay_category)
        return item

    async def get_garments(
        self, item_ids: list[str]
    ) -> dict[str, GarmentItem]:
        """Batch-fetch garments by ID in a single query.

        Args:
            item_ids: UUID strings. Non-UUID or unknown IDs are omitted.

        Returns:
            Mapping of item_id string to GarmentItem (all with valid
            overlay_category).

        Raises:
            ValueError: If any returned garment has invalid overlay_category.

        """
        items = await self._catalog.get_garments(item_ids)
        for item in items.values():
            _assert_overlay(item.overlay_category)
        return items

    async def list_garments(self, query: CatalogQuery) -> list[GarmentItem]:
        """List garments with optional filters.

        Args:
            query: CatalogQuery parameters.

        Returns:
            List of GarmentItems (all with valid overlay_category).

        Raises:
            ValueError: If any returned garment has invalid overlay_category.

        """
        items = await self._catalog.list_garments(query)
        for item in items:
            _assert_overlay(item.overlay_category)
        return items

    async def search(self, request: SearchRequest) -> list[GarmentSearchResult]:
        """Semantic catalog search.

        Args:
            request: SearchRequest with query/image_ref/filters/top_k.

        Returns:
            Ranked list of GarmentSearchResults.

        Raises:
            ValueError: If any result has invalid overlay_category.

        """
        results = await self._search.search(request)
        for r in results:
            _assert_overlay(r.overlay_category)
        return results

    async def get_logistics(self, item_id: str) -> GarmentLogistics:
        """Return logistics enrichment for a catalog item.

        Args:
            item_id: UUID string.

        Returns:
            GarmentLogistics populated from DB; availability_status='unknown'
            if no inventory rows exist.

        """
        rows = await self._db.fetch(
            """
            SELECT
                bi.display_price,
                bi.currency,
                bi.availability_status,
                bi.stock_on_hand,
                bi.available_to_promise,
                bi.pickup_eta_minutes,
                bi.fulfillment_options,
                b.branch_id::text,
                b.name AS branch_name,
                b.city
            FROM vto_branch_inventory bi
            JOIN vto_branches b ON b.branch_id = bi.branch_id
            WHERE bi.item_id=$1::uuid
            """,
            item_id,
        )

        if not rows:
            return GarmentLogistics(item_id=item_id)

        branches: list[BranchAvailability] = []
        display_price: float | None = None
        currency: str | None = None
        best_status = "unknown"

        _priority = {
            "in_stock": 0,
            "low_stock": 1,
            "out_of_stock": 2,
            "unknown": 3,
        }

        for row in rows:
            branches.append(
                BranchAvailability(
                    branch_id=row["branch_id"],
                    branch_name=row["branch_name"],
                    city=row["city"],
                    stock_on_hand=row.get("stock_on_hand"),
                    available_to_promise=row.get("available_to_promise"),
                    pickup_eta_minutes=row.get("pickup_eta_minutes"),
                    fulfillment_options=list(row["fulfillment_options"] or []),
                )
            )
            status = row.get("availability_status", "unknown")
            if _priority.get(status, 3) < _priority.get(best_status, 3):
                best_status = status
                display_price = (
                    float(row["display_price"])
                    if row.get("display_price")
                    else None
                )
                currency = row.get("currency")

        preferred = branches[0].branch_id if branches else None

        return GarmentLogistics(
            item_id=item_id,
            display_price=display_price,
            currency=currency,
            availability_status=best_status,  # type: ignore[arg-type]
            available_branches=branches,
            preferred_branch_id=preferred,
        )

    async def record_tryon(
        self, session_id: str, item_id: str, snapshot_id: str | None = None
    ) -> None:
        """Persist a try-on history record.

        Args:
            session_id: Runtime context UUID.
            item_id: Garment UUID tried on.
            snapshot_id: Optional snapshot artifact UUID.

        """
        await self._db.execute(
            """
            INSERT INTO vto_tryon_history (session_id, item_id, snapshot_id)
            VALUES ($1::uuid, $2::uuid, $3::uuid)
            ON CONFLICT DO NOTHING
            """,
            session_id,
            item_id,
            snapshot_id,
        )


def _assert_overlay(overlay_category: str) -> None:
    """Raise ValueError if overlay_category is not in the allowed set."""
    if overlay_category not in _VALID_OVERLAY:
        raise ValueError(
            f"overlay_category must be one of {_VALID_OVERLAY}, "
            f"got {overlay_category!r}"
        )
