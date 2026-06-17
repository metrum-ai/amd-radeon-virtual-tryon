# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""FastAPI routes: styling recommendations via OpenClaw agents."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from virtual_tryon.adapters.garment_catalog_adapter import GarmentCatalogAdapter
from virtual_tryon.api.routes import agent as agent_proxy
from virtual_tryon.catalog.gender_filter import catalog_gender, gender_matches
from virtual_tryon.storage.schemas import (
    GarmentItem,
    GarmentLogistics,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/vto", tags=["styling"])


# ------------------------------------------------------------------ #
# Request / response models
# ------------------------------------------------------------------ #


class StylingRequest(BaseModel):
    """Request body for the styling recommendations endpoint."""

    session_id: str = Field(..., description="Runtime context ID")
    garment_ids: list[str] = Field(
        default_factory=list,
        description="Currently selected garment IDs (for complement suggestions)",
    )
    context: str | None = Field(
        None, description="Occasion hint: casual, formal, smart-casual, …"
    )
    query: str | None = Field(
        None,
        description="Free-text preference query forwarded to catalog search",
    )
    customer_gender: str | None = Field(
        None, description="Selected customer catalog gender: women or men"
    )


class RecommendationItem(BaseModel):
    """Single recommendation card with garment metadata and logistics."""

    garment: GarmentItem
    recommendation_id: str
    overlay_category: str
    logistics: GarmentLogistics | None = None
    recommendation_type: str = "primary"
    reasoning: str = ""
    score: float = 1.0
    selectable: bool = True


class StylingResponse(BaseModel):
    """Response from the styling recommendations endpoint."""

    recommendations: list[RecommendationItem]
    styling_score: float
    suggestions: list[str]


# ------------------------------------------------------------------ #
# Dependency placeholder — wired in app.py
# ------------------------------------------------------------------ #


def _get_adapter() -> GarmentCatalogAdapter:
    """FastAPI dependency — replaced in app startup with real instance."""
    raise NotImplementedError("GarmentCatalogAdapter not wired")


# ------------------------------------------------------------------ #
# Route
# ------------------------------------------------------------------ #


@router.post("/styling/recommend", response_model=StylingResponse)
async def styling_recommend(
    request: StylingRequest,
    adapter: GarmentCatalogAdapter = Depends(_get_adapter),
) -> StylingResponse:
    """Return styling recommendations for the current session.

    Uses OpenClaw FashionStylistAgent for recommendation choice, then resolves
    returned catalog IDs into dashboard cards.

    Args:
        request: StylingRequest with session_id, garment_ids, context, query.
        adapter: Injected GarmentCatalogAdapter.

    Returns:
        StylingResponse with up to 5 selectable recommendation cards.

    Raises:
        HTTPException: 500 if catalog service unavailable.

    """
    query_text = request.query or _context_to_query(request.context)

    try:
        recs = await _openclaw_recommendations(adapter, request, query_text)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Styling recommendation error: %s", exc)
        raise HTTPException(
            status_code=500, detail="Styling service error"
        ) from exc

    return StylingResponse(
        recommendations=recs,
        styling_score=_avg_score(recs),
        suggestions=_build_suggestions(request.context, recs),
    )


# ------------------------------------------------------------------ #
# OpenClaw implementation
# ------------------------------------------------------------------ #


async def _openclaw_recommendations(
    adapter: GarmentCatalogAdapter,
    request: StylingRequest,
    query_text: str,
) -> list[RecommendationItem]:
    """Build recommendations from OpenClaw FashionStylistAgent output.

    Args:
        adapter: GarmentCatalogAdapter.
        request: Styling request context.
        query_text: Shopper preference query.

    Returns:
        List of OpenClaw-selected RecommendationItems with logistics.

    """
    chat_response = await agent_proxy.agent_chat(
        agent_proxy.AgentChatRequest(
            model="openclaw/fashion-stylist",
            session_id=request.session_id,
            customer_gender=catalog_gender(request.customer_gender),
            messages=[
                agent_proxy.ChatMessage(
                    role="user",
                    content=_styling_prompt(request, query_text),
                )
            ],
        )
    )
    content = _assistant_content(chat_response)
    recommendation_ids = _recommendation_ids(content)
    return await _recommendation_cards(adapter, recommendation_ids, request)


async def _recommendation_cards(
    adapter: GarmentCatalogAdapter,
    item_ids: list[str],
    request: StylingRequest,
) -> list[RecommendationItem]:
    """Resolve OpenClaw-selected item IDs into dashboard cards."""
    recs: list[RecommendationItem] = []
    for rank, iid in enumerate(item_ids[:3]):
        if iid in request.garment_ids:
            continue
        item = await adapter.get_garment(iid)
        if item is None:
            continue
        if not gender_matches(item.gender, request.customer_gender):
            continue
        logistics: GarmentLogistics | None = None
        try:
            logistics = await adapter.get_logistics(iid)
        except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
            logger.warning(
                "Failed to get logistics for item %s: %s", iid, exc
            )

        rec_type = "primary" if rank < 3 else "alternative"
        if (
            logistics is not None
            and logistics.availability_status == "out_of_stock"
        ):
            rec_type = "secondary"

        recs.append(
            RecommendationItem(
                garment=item,
                recommendation_id=f"rec-{iid[:8]}",
                overlay_category=item.overlay_category,
                logistics=logistics,
                recommendation_type=rec_type,
                reasoning=_build_reasoning(item, request.context),
                score=round(1.0 - rank * 0.05, 2),
                selectable=True,
            )
        )

    return recs


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _styling_prompt(request: StylingRequest, query_text: str) -> str:
    """Return the OpenClaw prompt for structured styling output."""
    selected = ", ".join(request.garment_ids) or "none"
    gender = catalog_gender(request.customer_gender)
    gender_instruction = (
        f'catalog gender_filter: "{gender}" (recommend only {gender} or unisex); '
        if gender
        else ""
    )
    return (
        "Use the VTO catalog tools and return only JSON with a "
        "`recommendations` array containing at most 3 items. Each item must "
        "include item_id, overlay_category, recommendation_type, reasoning, "
        "and score. No prose outside JSON. "
        f"{gender_instruction}"
        f"session_id: {request.session_id}; selected_item_ids: {selected}; "
        f"shopper_request: {query_text}"
    )


def _assistant_content(response: Any) -> str:
    """Extract assistant text from an OpenAI-compatible response."""
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


def _recommendation_ids(content: str) -> list[str]:
    """Parse recommendation item IDs from OpenClaw JSON output."""
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE)
    candidate = (fenced.group(1) if fenced else content).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return []

    if isinstance(parsed, list):
        recommendations = parsed
    elif isinstance(parsed, dict):
        recommendations = parsed.get("recommendations", [])
    else:
        return []
    if not isinstance(recommendations, list):
        return []

    item_ids: list[str] = []
    for item in recommendations:
        item_id = item if isinstance(item, str) else item.get("item_id")
        if isinstance(item_id, str) and item_id not in item_ids:
            item_ids.append(item_id)
    return item_ids


def _context_to_query(context: str | None) -> str:
    mapping = {
        "casual": "casual everyday comfortable",
        "formal": "formal office business",
        "smart-casual": "smart casual polished",
        "party": "party evening stylish",
        "traditional": "traditional ethnic cultural",
        "outdoor": "outdoor layer jacket",
    }
    return (
        mapping.get(context or "", "clothing garment")
        if context
        else "clothing garment"
    )


def _build_reasoning(item: GarmentItem, context: str | None) -> str:
    parts = [item.name]
    if item.color:
        parts.append(item.color)
    if context:
        parts.append(f"suits {context} occasion")
    return ", ".join(parts)[:120]


def _avg_score(recs: list[RecommendationItem]) -> float:
    if not recs:
        return 0.0
    return round(sum(r.score for r in recs) / len(recs), 2)


def _build_suggestions(
    context: str | None, recs: list[RecommendationItem]
) -> list[str]:
    if not recs:
        return []
    hints = []
    cats = {r.overlay_category for r in recs}
    if "tops" in cats and "bottoms" not in cats:
        hints.append("Add a bottom to complete the outfit.")
    if context == "formal":
        hints.append("Consider adding outerwear for a polished finish.")
    return hints
