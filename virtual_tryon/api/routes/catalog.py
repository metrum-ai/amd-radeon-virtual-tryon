# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""FastAPI routes: catalog list, search, item detail, logistics availability."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from virtual_tryon.adapters.garment_catalog_adapter import GarmentCatalogAdapter
from virtual_tryon.storage.schemas import (
    CatalogQuery,
    GarmentItem,
    GarmentLogistics,
    GarmentSearchResult,
    SearchRequest,
)

router = APIRouter(prefix="/api/v1/vto", tags=["catalog"])


def _get_adapter() -> GarmentCatalogAdapter:
    """FastAPI dependency — replaced in app startup with real instance."""
    raise NotImplementedError("GarmentCatalogAdapter not wired")


@router.get("/catalog", response_model=list[GarmentItem])
async def list_catalog(
    category: str | None = Query(None),
    subcategory: str | None = Query(None),
    overlay_category: str | None = Query(None),
    gender: str | None = Query(None),
    color: str | None = Query(None),
    brand: str | None = Query(None),
    min_price: float | None = Query(None),
    max_price: float | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    adapter: GarmentCatalogAdapter = Depends(_get_adapter),
) -> list[GarmentItem]:
    """List catalog garments with optional filters.

    Args:
        category: Filter by category.
        subcategory: Filter by subcategory.
        overlay_category: Filter by overlay zone (tops/bottoms/one-pieces).
        color: Filter by color.
        brand: Filter by brand.
        min_price: Minimum price filter.
        max_price: Maximum price filter.
        limit: Page size (max 100).
        offset: Page offset.
        adapter: Injected GarmentCatalogAdapter.

    Returns:
        List of GarmentItem objects.

    """
    q = CatalogQuery(
        category=category,
        subcategory=subcategory,
        overlay_category=overlay_category,
        gender=gender,
        color=color,
        brand=brand,
        min_price=min_price,
        max_price=max_price,
        limit=limit,
        offset=offset,
    )
    return await adapter.list_garments(q)


@router.post("/catalog/search", response_model=list[GarmentSearchResult])
async def search_catalog(
    request: SearchRequest,
    adapter: GarmentCatalogAdapter = Depends(_get_adapter),
) -> list[GarmentSearchResult]:
    """Semantic catalog search by text query and/or image reference.

    Args:
        request: SearchRequest body.
        adapter: Injected GarmentCatalogAdapter.

    Returns:
        Ranked list of GarmentSearchResult objects.

    """
    return await adapter.search(request)


@router.get("/catalog/{item_id}", response_model=GarmentItem)
async def get_catalog_item(
    item_id: str,
    adapter: GarmentCatalogAdapter = Depends(_get_adapter),
) -> GarmentItem:
    """Fetch a single catalog garment by ID.

    Args:
        item_id: Garment UUID.
        adapter: Injected GarmentCatalogAdapter.

    Returns:
        GarmentItem.

    Raises:
        HTTPException: 404 if garment not found.

    """
    item = await adapter.get_garment(item_id)
    if item is None:
        raise HTTPException(
            status_code=404, detail=f"Garment {item_id!r} not found"
        )
    return item


@router.get("/logistics/availability", response_model=GarmentLogistics)
async def get_logistics_availability(
    item_id: str = Query(..., description="Garment UUID"),
    adapter: GarmentCatalogAdapter = Depends(_get_adapter),
) -> GarmentLogistics:
    """Return price and branch availability for a catalog garment.

    Args:
        item_id: Garment UUID.
        adapter: Injected GarmentCatalogAdapter.

    Returns:
        GarmentLogistics enrichment.

    """
    return await adapter.get_logistics(item_id)
