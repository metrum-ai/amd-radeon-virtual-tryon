# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Pydantic schemas for F4 Garment Catalog, Logistics, and Search."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

_VALID_OVERLAY = {"tops", "bottoms", "one-pieces"}
_VALID_STATUS = {"in_stock", "low_stock", "out_of_stock", "unknown"}


class GarmentItem(BaseModel):
    """Catalog garment row."""

    item_id: str | None = None
    name: str
    category: str
    subcategory: str | None = None
    overlay_category: str
    gender: str = "unisex"
    brand: str | None = None
    color: str | None = None
    size_range: list[str] | None = None
    price: float | None = None
    description: str | None = None
    image_path: str
    embedding_id: str | None = None
    logistics_ref: str | None = None
    branch_inventory_ref: str | None = None
    metadata: dict | None = None

    @field_validator("overlay_category")
    @classmethod
    def validate_overlay_category(cls, v: str) -> str:
        """Enforce overlay_category is one of the three warp-engine zones."""
        if v not in _VALID_OVERLAY:
            raise ValueError(
                f"overlay_category must be one of {_VALID_OVERLAY}, got {v!r}"
            )
        return v


class CatalogQuery(BaseModel):
    """Filter parameters for catalog list endpoint."""

    category: str | None = None
    subcategory: str | None = None
    overlay_category: str | None = None
    gender: str | None = None
    color: str | None = None
    brand: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    limit: int = 20
    offset: int = 0


class SearchRequest(BaseModel):
    """Request body for semantic catalog search."""

    query: str | None = None
    image_ref: str | None = None
    filters: CatalogQuery | None = None
    top_k: int = 20


class GarmentSearchResult(BaseModel):
    """Single result returned by SearchService / GarmentCatalogAdapter."""

    item_id: str
    name: str
    category: str
    overlay_category: str
    gender: str = "unisex"
    image_path: str
    brand: str | None = None
    color: str | None = None
    price: float | None = None
    score: float = 0.0

    @field_validator("overlay_category")
    @classmethod
    def validate_overlay_category(cls, v: str) -> str:
        """Guard: results with invalid overlay_category must never leave adapter."""
        if v not in _VALID_OVERLAY:
            raise ValueError(
                f"overlay_category must be one of {_VALID_OVERLAY}, got {v!r}"
            )
        return v


class BranchAvailability(BaseModel):
    """Per-branch stock and logistics info."""

    branch_id: str
    branch_name: str
    city: str
    stock_on_hand: int | None = None
    available_to_promise: int | None = None
    pickup_eta_minutes: int | None = None
    fulfillment_options: list[str] = []


class GarmentLogistics(BaseModel):
    """Logistics enrichment for a catalog item."""

    item_id: str
    display_price: float | None = None
    currency: str | None = None
    discount_label: str | None = None
    availability_status: Literal[
        "in_stock", "low_stock", "out_of_stock", "unknown"
    ] = "unknown"
    available_branches: list[BranchAvailability] = []
    preferred_branch_id: str | None = None
    fulfillment_options: list[str] = []
    stale: bool = False
    unavailable_reason: str | None = None
