# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Hybrid semantic search: Milvus ANN + PostgreSQL metadata join."""

from __future__ import annotations

import asyncio

from virtual_tryon.catalog.catalog_service import CatalogService
from virtual_tryon.catalog.garment_embedder import GarmentEmbedder
from virtual_tryon.catalog.gender_filter import gender_matches
from virtual_tryon.storage.milvus_store import MilvusStore
from virtual_tryon.storage.schemas import (
    CatalogQuery,
    GarmentItem,
    GarmentSearchResult,
    SearchRequest,
)


class SearchService:
    """Hybrid text + visual semantic search over the garment catalog.

    Args:
        catalog: CatalogService for PostgreSQL metadata lookup.
        embedder: GarmentEmbedder for query vectorisation.
        text_store: MilvusStore for text embeddings.
        visual_store: MilvusStore for visual embeddings.

    """

    def __init__(
        self,
        catalog: CatalogService,
        embedder: GarmentEmbedder,
        text_store: MilvusStore,
        visual_store: MilvusStore,
    ) -> None:
        """Initialize the SearchService."""
        self._catalog = catalog
        self._embedder = embedder
        self._text_store = text_store
        self._visual_store = visual_store

    async def search(self, request: SearchRequest) -> list[GarmentSearchResult]:
        """Execute hybrid search and return ranked results.

        Text query → text collection; image_ref → visual collection.
        Both → scores averaged for items appearing in both result sets.

        Args:
            request: SearchRequest with optional query, image_ref, filters, top_k.

        Returns:
            List of GarmentSearchResult sorted by descending score, max top_k items.

        """
        if not request.query and not request.image_ref:
            filters = (
                request.filters.model_copy()
                if request.filters
                else CatalogQuery()
            )
            filters.limit = request.top_k
            items = await self._catalog.list_garments(filters)
            return [
                GarmentSearchResult(
                    item_id=str(item.item_id),
                    name=item.name,
                    category=item.category,
                    overlay_category=item.overlay_category,
                    gender=item.gender,
                    image_path=item.image_path,
                    brand=item.brand,
                    color=item.color,
                    price=item.price,
                    score=1.0,
                )
                for item in items
            ]

        # PG text fallback: no embedder → fall back to catalog text search
        if request.query and self._embedder is None:
            filters = (
                request.filters.model_copy()
                if request.filters
                else CatalogQuery()
            )
            filters.limit = request.top_k
            items = await self._catalog.list_garments(
                filters, text_query=request.query
            )
            return [
                GarmentSearchResult(
                    item_id=str(item.item_id),
                    name=item.name,
                    category=item.category,
                    overlay_category=item.overlay_category,
                    gender=item.gender,
                    image_path=item.image_path,
                    brand=item.brand,
                    color=item.color,
                    price=item.price,
                    score=1.0,
                )
                for item in items
            ]

        scores: dict[str, float] = {}
        hit_count: dict[str, int] = {}
        search_top_k = request.top_k * 4 if request.filters else request.top_k

        if request.query and self._embedder is not None:
            # Torch embedding is CPU/GPU-bound; run it off the event loop so
            # concurrent requests are not blocked during inference.
            text_vec = await asyncio.to_thread(
                self._embedder.embed_text, request.query
            )
            hits = self._text_store.search(text_vec, top_k=search_top_k)
            for hit in hits:
                iid = hit["item_id"]
                scores[iid] = scores.get(iid, 0.0) + hit["score"]
                hit_count[iid] = hit_count.get(iid, 0) + 1

        if request.image_ref and self._embedder is not None:
            visual_vec = await asyncio.to_thread(
                self._embedder.embed_image, request.image_ref
            )
            hits = self._visual_store.search(visual_vec, top_k=search_top_k)
            for hit in hits:
                iid = hit["item_id"]
                scores[iid] = scores.get(iid, 0.0) + hit["score"]
                hit_count[iid] = hit_count.get(iid, 0) + 1

        avg_scores = {iid: scores[iid] / hit_count[iid] for iid in scores}
        ranked = sorted(avg_scores.items(), key=lambda x: x[1], reverse=True)
        top_ids = [iid for iid, _ in ranked[:search_top_k]]

        results: list[GarmentSearchResult] = []
        for item_id in top_ids:
            item = await self._catalog.get_garment(item_id)
            if item is None:
                continue
            if not _matches_filters(item, request.filters):
                continue
            results.append(
                GarmentSearchResult(
                    item_id=str(item.item_id),
                    name=item.name,
                    category=item.category,
                    overlay_category=item.overlay_category,
                    gender=item.gender,
                    image_path=item.image_path,
                    brand=item.brand,
                    color=item.color,
                    price=item.price,
                    score=avg_scores[item_id],
                )
            )
            if len(results) == request.top_k:
                break

        # Vector index empty (e.g. catalog seeded without Milvus) — fall back to PG text search
        if not results and request.query:
            filters = (
                request.filters.model_copy()
                if request.filters
                else CatalogQuery()
            )
            filters.limit = request.top_k
            items = await self._catalog.list_garments(
                filters, text_query=request.query
            )
            return [
                GarmentSearchResult(
                    item_id=str(item.item_id),
                    name=item.name,
                    category=item.category,
                    overlay_category=item.overlay_category,
                    gender=item.gender,
                    image_path=item.image_path,
                    brand=item.brand,
                    color=item.color,
                    price=item.price,
                    score=1.0,
                )
                for item in items
            ]

        return results


def _matches_filters(item: GarmentItem, filters: CatalogQuery | None) -> bool:
    """Return whether a vector-search hit satisfies catalog filters."""
    if filters is None:
        return True

    for field in ("category", "subcategory", "overlay_category", "brand"):
        expected = getattr(filters, field)
        if expected is not None and getattr(item, field) != expected:
            return False

    if filters.gender is not None and not gender_matches(
        item.gender, filters.gender
    ):
        return False
    if filters.color is not None:
        if (
            item.color is None
            or filters.color.lower() not in item.color.lower()
        ):
            return False
    if filters.min_price is not None:
        if item.price is None or item.price < filters.min_price:
            return False
    if filters.max_price is not None:
        if item.price is None or item.price > filters.max_price:
            return False

    return True
