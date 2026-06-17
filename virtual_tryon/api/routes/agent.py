# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Agent chat proxy for the OpenClaw Gateway."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from virtual_tryon.adapters.garment_catalog_adapter import GarmentCatalogAdapter
from virtual_tryon.storage.schemas import (
    CatalogQuery,
    GarmentSearchResult,
    SearchRequest,
)

router = APIRouter(prefix="/api/v1/vto/agent", tags=["agent"])

_OPENCLAW_AGENT_IDS = frozenset(
    {
        "tryon-orchestrator",
        "logistics-agent",
        "fashion-stylist",
        "customer-feedback-agent",
    }
)

_WELCOME_MESSAGE = (
    "Welcome to your personalized virtual try-on experience. "
    "What occasion or style are you shopping for today?"
)


def _get_adapter() -> GarmentCatalogAdapter:
    """FastAPI dependency — replaced at startup by app.dependency_overrides.

    Raises:
        RuntimeError: If called before app startup wires the real adapter.

    """
    raise RuntimeError("GarmentCatalogAdapter not wired — app not started yet")


class ChatMessage(BaseModel):
    """Single OpenAI-compatible chat message."""

    role: str
    content: str


class AgentChatRequest(BaseModel):
    """Request body for VTO agent chat."""

    model: str = Field(default="openclaw/default")
    messages: list[ChatMessage]
    stream: bool = False
    session_id: str | None = None
    customer_gender: str | None = None
    user: str | None = None


class AgentChatResponse(BaseModel):
    """OpenAI-compatible response wrapper."""

    choices: list[dict[str, Any]]
    model: str


@dataclass(frozen=True)
class _ShoppingIntent:
    """LLM-extracted shopping intent for RAG-driven catalog search."""

    search_query: str
    overlay_categories: tuple[str, ...]
    is_outfit_request: bool


@dataclass(frozen=True)
class LogisticsConversationState:
    """Structured item context for logistics follow-up questions."""

    selected_item_id: str | None
    recommendation_item_ids: tuple[str, ...]


@router.post("/chat", response_model=None)
async def agent_chat(
    request: AgentChatRequest,
    adapter: GarmentCatalogAdapter = Depends(_get_adapter),
) -> dict[str, Any] | StreamingResponse:
    """Forward a chat request to the OpenClaw Gateway.

    Injects live catalog or logistics context before forwarding so agents
    produce grounded responses without relying on tool calls for every turn.
    Deterministic helpers serve as fallbacks on OpenClaw errors or invalid IDs.

    Args:
        request: OpenAI-compatible chat request from the dashboard.
        adapter: Injected GarmentCatalogAdapter for catalog pre-fetch.

    Returns:
        The OpenClaw Gateway response, or a deterministic fallback.

    Raises:
        HTTPException: If OpenClaw is not configured or is unreachable.

    """
    agent = _agent_id(request.model)
    latest = _latest_user_text(request)
    is_logistics = agent == "logistics-agent" or (
        agent in (None, "tryon-orchestrator", "fashion-stylist")
        and _is_logistics_intent(latest)
    )

    # Prefetch context and inject into system message before calling OpenClaw.
    if is_logistics:
        enriched = await _inject_logistics_context(request, adapter)
    elif agent in (None, "tryon-orchestrator", "fashion-stylist"):
        enriched = await _inject_catalog_context(request, adapter)
    else:
        enriched = request

    try:
        payload = _gateway_payload(enriched)
        headers = _gateway_headers(enriched)
    except HTTPException:
        return await _deterministic_fallback(request, adapter, is_logistics)

    url = _gateway_chat_url()

    if request.stream:
        return StreamingResponse(
            _stream_openclaw_with_fallback(
                url, headers, payload, request, adapter, is_logistics
            ),
            media_type="text/event-stream",
        )

    try:
        async with httpx.AsyncClient(timeout=_gateway_timeout()) as client:
            response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPStatusError, httpx.RequestError):
        return await _deterministic_fallback(request, adapter, is_logistics)

    return await _validate_openclaw_response(
        data, request, adapter, is_logistics
    )


def _gender_default_cats(gender: str | None) -> tuple[str, ...]:
    """Return gender-aware default overlay categories."""
    return (
        ("one-pieces",) if _catalog_gender(gender) == "women" else ("tops",)
    )


def _fallback_intent(query: str, gender: str | None) -> _ShoppingIntent:
    """Fast rule-based intent fallback when the LLM is unavailable or slow.

    Covers common garment keywords.  Anything that does not match falls back
    to gender-aware defaults so the flow never stalls.

    Args:
        query: Raw user query text.
        gender: Catalog gender value ("women", "men", or None).

    Returns:
        Parsed _ShoppingIntent.

    """
    lowered = query.lower()

    if _is_outfit_intent(lowered):
        return _ShoppingIntent(query, ("tops", "bottoms"), True)

    if any(
        w in lowered
        for w in (
            "dress",
            "gown",
            "jumpsuit",
            "one piece",
            "one-piece",
            "onepiece",
        )
    ):
        return _ShoppingIntent(query, ("one-pieces",), False)

    if any(
        w in lowered
        for w in (
            "top",
            "shirt",
            "blouse",
            "t-shirt",
            "tshirt",
            "jacket",
            "sweater",
            "hoodie",
        )
    ):
        return _ShoppingIntent(query, ("tops",), False)

    if any(
        w in lowered
        for w in (
            "bottom",
            "pants",
            "trousers",
            "skirt",
            "jeans",
            "shorts",
        )
    ):
        return _ShoppingIntent(query, ("bottoms",), False)

    return _ShoppingIntent(
        query,
        _gender_default_cats(gender),
        False,
    )


async def _extract_shopping_intent(
    query: str,
    gender: str | None,
    client: httpx.AsyncClient | None,
) -> _ShoppingIntent:
    """Extract structured shopping intent using the local LLM.

    Falls back to fast rule-based heuristics on any error or timeout so the
    calling path always gets a usable intent without raising.

    The LLM path understands diverse natural-language queries (e.g.
    "something fancy for a wedding") that keyword matching cannot capture.
    The rule-based fallback guarantees sub-second response when the model is
    cold or the system is under load.

    Args:
        query: Raw user query text.
        gender: Catalog gender value ("women", "men", or None).
        client: Optional shared httpx async client.

    Returns:
        Parsed _ShoppingIntent from the LLM, or a rule-based fallback.

    """
    base = os.environ.get(
        "OLLAMA_BASE_URL", "http://ollama-metrics:8080"
    ).rstrip("/")
    model = os.environ.get("OLLAMA_LLM_MODEL", "qwen3.6:27b")

    user_prompt = (
        f"Query: {query}\n"
        f"Customer gender: {gender or 'any'}\n\n"
        "Rules:\n"
        '- "outfit", "look", "style", "combination", "set" cues'
        " → is_outfit_request: true\n"
        '- Explicit garment ("dress", "gown", "jumpsuit")'
        ' → overlay_categories: ["one-pieces"]\n'
        '- Explicit top ("shirt", "blouse", "top", "jacket")'
        ' → overlay_categories: ["tops"]\n'
        '- Explicit bottom ("trousers", "skirt", "jeans", "pants")'
        ' → overlay_categories: ["bottoms"]\n'
        "- Color/occasion only, gender=female"
        ' → overlay_categories: ["one-pieces"]\n'
        "- Color/occasion only, gender=male"
        ' → overlay_categories: ["tops"]\n'
        "- Enrich search_query with all style/color/occasion details.\n\n"
        'Return: {"search_query":"...","overlay_categories":[],'
        '"is_outfit_request":false}'
    )

    try:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Extract garment shopping intent. "
                        "Return JSON only. No prose."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "max_tokens": 128,
        }
        _client = client or httpx.AsyncClient()
        resp = await _client.post(
            f"{base}/v1/chat/completions",
            json=payload,
            timeout=_intent_timeout(),
        )
        if client is None:
            await _client.aclose()
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON in LLM response")
        data = json.loads(match.group(0))

        valid_cats = {"tops", "bottoms", "one-pieces"}
        cats = tuple(
            c for c in data.get("overlay_categories", []) if c in valid_cats
        )
        is_outfit = bool(data.get("is_outfit_request", False))
        raw_sq = data.get("search_query", query)
        search_query = str(raw_sq).strip() if raw_sq else query

        if not cats and not is_outfit:
            cats = _gender_default_cats(gender)

        return _ShoppingIntent(
            search_query=search_query or query,
            overlay_categories=cats,
            is_outfit_request=is_outfit,
        )
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, KeyError):
        return _fallback_intent(query, gender)


def _grounded_query(messages: list[ChatMessage]) -> str:
    """Combine recent user messages into a single catalog search query.

    Args:
        messages: Full conversation message list.

    Returns:
        Space-joined user message contents, excluding welcome prompts.

    """
    return " ".join(
        m.content
        for m in messages[-12:]
        if m.role == "user" and not m.content.lower().startswith("welcome")
    )


async def _build_grounded_reply(
    adapter: GarmentCatalogAdapter,
    query: str,
    history: list[ChatMessage],
) -> str:
    """Return a plain-text catalog-grounded reply for a given query.

    Routes to logistics or styling based on query intent. Used as the
    deterministic fallback path and for direct catalog responses.

    Args:
        adapter: Wired GarmentCatalogAdapter.
        query: Latest user query text.
        history: Prior assistant messages for context resolution.

    Returns:
        Plain-text response string.

    """
    if _is_logistics_intent(query):
        return await _logistics_grounded_text(adapter, query)
    return await _styling_grounded_text(adapter, query)


async def _logistics_grounded_text(
    adapter: GarmentCatalogAdapter,
    query: str,
) -> str:
    """Return branch availability text for the item referenced in query.

    Args:
        adapter: Wired GarmentCatalogAdapter.
        query: User query containing item name and/or city reference.

    Returns:
        Formatted availability string.

    """
    results = await adapter.search(
        SearchRequest(
            query=query,
            top_k=1,
            filters=CatalogQuery(limit=1),
        )
    )
    if not results:
        return (
            "Which catalog item do you want pricing for? "
            "You can use the item name, such as Ruby Red Halter Top."
        )
    item_id = results[0].item_id
    garment, logistics = await asyncio.gather(
        adapter.get_garment(item_id),
        adapter.get_logistics(item_id),
    )
    name = garment.name if garment else str(item_id)
    city = _requested_city(query)

    if city:
        branch = next(
            (
                b
                for b in logistics.available_branches
                if b.city.lower() == city.lower()
            ),
            None,
        )
        if branch and (branch.available_to_promise or 0) > 0:
            return (
                f"{name} is available at "
                f"{branch.branch_name} ({branch.city}): "
                f"ATP {branch.available_to_promise}."
            )
        return f"{name} is not available in the {city} branch."

    in_stock = [
        b
        for b in logistics.available_branches
        if (b.available_to_promise or 0) > 0
    ]
    if in_stock:
        b = in_stock[0]
        return (
            f"{name} is available at "
            f"{b.branch_name} ({b.city}): ATP {b.available_to_promise}."
        )
    return f"{name}: {logistics.availability_status}."


async def _styling_grounded_text(
    adapter: GarmentCatalogAdapter,
    query: str,
) -> str:
    """Return a catalog-backed styling recommendation as plain text.

    Args:
        adapter: Wired GarmentCatalogAdapter.
        query: User query describing the desired style or occasion.

    Returns:
        Formatted recommendation string mentioning the Recommendations panel.

    """
    items = await adapter.search(
        SearchRequest(
            query=query,
            top_k=3,
            filters=CatalogQuery(limit=3),
        )
    )
    if not items:
        return "What type of look are you going for today?"

    is_outfit = any(
        w in query.lower()
        for w in ("outfit", "look", "combination", "combo", "set")
    )
    tops = [it for it in items if it.overlay_category == "tops"]
    bottoms = [it for it in items if it.overlay_category == "bottoms"]

    if (is_outfit or (tops and bottoms)) and tops and bottoms:
        return (
            "Here is a complete outfit for you:\n"
            f"• {tops[0].name}\n"
            f"• {bottoms[0].name}\n\n"
            "Check the Recommendations panel to try these on."
        )

    return (
        f"Here is {items[0].name} for you. "
        "Check the Recommendations panel to try it on."
    )


async def _fashion_stylist_chat_response(
    request: AgentChatRequest,
    adapter: GarmentCatalogAdapter,
) -> dict[str, Any] | StreamingResponse:
    """Return catalog-backed styling output without exposing raw agent JSON.

    The UI needs valid catalog IDs, not speculative item names. This
    deterministic path preserves the chat-completions contract while keeping
    recommendations grounded in the adapter.

    Args:
        request: Incoming agent chat request.
        adapter: Wired GarmentCatalogAdapter.

    Returns:
        OpenAI-compatible chat response or stream.

    """
    latest = _latest_user_text(request)
    if _is_logistics_intent(latest):
        return await _logistics_chat_response(request, adapter)

    if _is_welcome_intent(latest.lower()):
        content = _WELCOME_MESSAGE
    else:
        gender = _catalog_gender(request.customer_gender)
        user_text = _recent_user_text(request)
        question = _preference_question(user_text, gender)
        if question:
            content = question
        else:
            items = await _styling_items_for_request(
                adapter,
                user_text,
                latest.lower(),
                gender,
            )
            content = (
                _recommendation_json(items)
                if items
                else "What type of look are you going for today?"
            )

    if request.stream:
        return StreamingResponse(
            _stream_chat_text(content),
            media_type="text/event-stream",
        )
    return _chat_text_response(content, request.model)


def _recent_user_text(request: AgentChatRequest) -> str:
    """Return recent user messages as a single search string."""
    return " ".join(
        message.content
        for message in request.messages[-12:]
        if message.role == "user"
        and not message.content.lower().startswith("welcome")
    )


def _latest_user_text(request: AgentChatRequest) -> str:
    """Return the latest user message, if any."""
    user_msgs = [m.content for m in request.messages if m.role == "user"]
    return user_msgs[-1] if user_msgs else ""


def _catalog_gender(gender: str | None) -> str | None:
    """Normalize shopper gender labels to catalog filter values."""
    if gender in ("female", "women", "female-senior"):
        return "women"
    if gender in ("male", "men", "male-senior"):
        return "men"
    return gender


def _is_female_gender(gender: str | None) -> bool:
    """Return whether the catalog gender is for women-focused styling."""
    return _catalog_gender(gender) == "women"


def _is_welcome_intent(text: str) -> bool:
    """Return whether the latest message is a greeting or welcome prompt."""
    stripped = text.strip()
    if stripped.startswith("welcome"):
        return True
    return stripped in {"hi", "hello", "hey"}


def _is_logistics_intent(text: str) -> bool:
    """Return whether the shopper is asking for price or stock details."""
    lowered = text.lower()
    return any(
        term in lowered
        for term in (
            "availability",
            "available",
            "branch",
            "cost",
            "how much",
            "price",
            "stock",
        )
    )


# Common color names for preference-discovery heuristics.
_COLOR_TERMS = frozenset(
    {
        "black",
        "white",
        "red",
        "blue",
        "green",
        "yellow",
        "orange",
        "purple",
        "pink",
        "brown",
        "grey",
        "gray",
        "beige",
        "navy",
        "teal",
        "maroon",
        "olive",
        "cream",
        "ivory",
        "gold",
        "silver",
        "bronze",
        "magenta",
        "cyan",
        "coral",
        "burgundy",
        "charcoal",
        "khaki",
        "lavender",
        "lime",
        "mustard",
        "peach",
        "plum",
        "rose",
        "salmon",
        "tan",
        "turquoise",
        "violet",
    }
)

_OCCASION_TERMS = frozenset(
    {
        "casual",
        "formal",
        "business",
        "office",
        "dinner",
        "date",
        "party",
        "wedding",
        "beach",
        "summer",
        "work",
        "gym",
        "sport",
        "festive",
        "evening",
        "brunch",
        "night",
        "day",
        "weekend",
        "vacation",
        "holiday",
    }
)

# Specific garment-type keywords that resolve ambiguity.
# "outfit" and "combo" are intentionally excluded — they are too vague
# and should trigger a clarification question.
_WOMEN_GARMENT_TERMS = frozenset(
    {
        "dress",
        "gown",
        "jumpsuit",
        "one piece",
        "one-piece",
        "onepiece",
        "top",
        "shirt",
        "blouse",
        "t-shirt",
        "tshirt",
        "jacket",
        "sweater",
        "hoodie",
        "bottom",
        "pants",
        "trousers",
        "skirt",
        "jeans",
        "shorts",
        "suit",
    }
)

_MEN_GARMENT_TERMS = frozenset(
    {
        "shirt",
        "top",
        "t-shirt",
        "tshirt",
        "jacket",
        "blazer",
        "suit",
        "hoodie",
        "sweater",
        "bottom",
        "pants",
        "trousers",
        "jeans",
        "shorts",
    }
)


def _has_color_hint(text: str) -> bool:
    """Return whether the query mentions a color."""
    lowered = text.lower()
    return any(color in lowered for color in _COLOR_TERMS)


def _has_occasion_hint(text: str) -> bool:
    """Return whether the query mentions an occasion or style."""
    lowered = text.lower()
    return any(term in lowered for term in _OCCASION_TERMS)


def _has_garment_type_hint(text: str, gender: str | None) -> bool:
    """Return whether the query specifies a garment type."""
    lowered = text.lower()
    terms = _WOMEN_GARMENT_TERMS if _is_female_gender(gender) else _MEN_GARMENT_TERMS
    return any(term in lowered for term in terms)


def _preference_question(text: str, gender: str | None) -> str | None:
    """Return the next preference question, or None if all info is present.

    Args:
        text: Latest user message.
        gender: Catalog gender value.

    Returns:
        Plain-text question if a key preference is missing, otherwise None.

    """
    has_garment = _has_garment_type_hint(text, gender)
    has_color = _has_color_hint(text)
    has_occasion = _has_occasion_hint(text)

    if not has_garment:
        if _is_female_gender(gender):
            return (
                "Are you thinking a dress / one-piece, "
                "or a top and bottom combo?"
            )
        return (
            "Are you looking for a shirt, or a jacket and trousers combo?"
        )

    if not has_color and not has_occasion:
        return "What color do you prefer?"

    return None


def _is_outfit_intent(text: str) -> bool:
    """Return whether the query explicitly asks for an outfit / combo."""
    return any(
        term in text.lower()
        for term in (
            "outfit",
            "combination",
            "combo",
            "full outfit",
            "top and bottom",
            "top + bottom",
        )
    )


async def _styling_items_for_request(
    adapter: GarmentCatalogAdapter,
    text: str,
    latest_lower: str,
    gender: str | None,
) -> list[Any]:
    """Select catalog-backed items using LLM intent extraction + RAG.

    When the LLM is warm the intent path understands diverse natural-language
    queries (e.g.  "something fancy for a wedding").  When the LLM is cold or
    slow we fall back to fast rule-based heuristics, and if those heuristics
    produce only a gender default we also run a broad category-agnostic search
    so the shopper still sees relevant items.

    Args:
        adapter: Wired GarmentCatalogAdapter.
        text: Combined recent user messages for context.
        latest_lower: Latest user message in lowercase.
        gender: Catalog gender filter value.

    Returns:
        List of search results to surface as recommendations.

    """
    # Heuristic shortcut: obvious outfit keywords bypass slow LLM intent
    # extraction entirely.
    if _is_outfit_intent(latest_lower or text):
        top = await _search_overlay(adapter, text, gender, "tops", 1)
        bottom = await _search_overlay(adapter, text, gender, "bottoms", 1)
        return [*top, *bottom]

    intent = await _extract_shopping_intent(
        latest_lower or text, gender, None
    )

    if intent.is_outfit_request or len(intent.overlay_categories) > 1:
        top = await _search_overlay(
            adapter, intent.search_query, gender, "tops", 1
        )
        bottom = await _search_overlay(
            adapter, intent.search_query, gender, "bottoms", 1
        )
        return [*top, *bottom]

    cat = intent.overlay_categories[0] if intent.overlay_categories else None
    results = await _search_overlay(
        adapter, intent.search_query, gender, cat, 3
    )

    # If the intent extraction fell back to gender defaults (no explicit
    # garment keywords matched), also search across ALL categories so the
    # shopper sees the most semantically relevant items regardless of
    # overlay_category.
    if intent.overlay_categories == _gender_default_cats(gender):
        broad = await _search_overlay(
            adapter, intent.search_query, gender, None, 3
        )
        seen = {r.item_id for r in results}
        for item in broad:
            if item.item_id not in seen:
                results.append(item)
                if len(results) >= 6:
                    break

    return results[:6]


async def _search_overlay(
    adapter: GarmentCatalogAdapter,
    text: str,
    gender: str | None,
    overlay_category: str | None,
    limit: int,
) -> list[Any]:
    """Search one overlay category and return catalog results.

    Args:
        adapter: Wired GarmentCatalogAdapter.
        text: Natural language search query (may include color, occasion).
        gender: Catalog gender filter.
        overlay_category: Overlay category filter, or None for all.
        limit: Maximum number of results.

    Returns:
        Up to `limit` search results, or a catalog listing fallback.

    """
    results = await adapter.search(
        SearchRequest(
            query=text,
            top_k=limit,
            filters=CatalogQuery(
                gender=gender,
                overlay_category=overlay_category,
                limit=limit,
            ),
        )
    )
    if results:
        return results[:limit]

    return await adapter.list_garments(
        CatalogQuery(
            gender=gender,
            overlay_category=overlay_category,
            limit=limit,
        )
    )


def _recommendation_json(items: list[Any]) -> str:
    """Return structured recommendation JSON for valid catalog items."""
    recommendations = []
    for index, item in enumerate(items):
        recommendations.append(
            {
                "item_id": item.item_id,
                "overlay_category": item.overlay_category,
                "recommendation_type": (
                    "primary" if index == 0 else "complement"
                ),
                "reasoning": f"Catalog-backed pick: {item.name}",
                "score": round(max(0.7, 0.95 - (index * 0.08)), 2),
            }
        )
    return json.dumps(
        {
            "recommendations": recommendations,
            "styling_score": 0.9,
            "suggestions": [
                "These items are available in the current catalog."
            ],
        },
        indent=2,
    )


async def _inject_catalog_context(
    request: AgentChatRequest,
    adapter: GarmentCatalogAdapter,
) -> AgentChatRequest:
    """Pre-fetch catalog items and inject them into the system message.

    Uses LLM intent extraction to form a semantically enriched search query,
    then appends a <catalog_context> block to the first system message so the
    fashion-stylist agent can select recommendations without calling MCP tools.

    Returns the original request unchanged if the search fails, returns no
    items, or if the message is a meta/welcome prompt.

    Args:
        request: Incoming agent chat request.
        adapter: Wired GarmentCatalogAdapter.

    Returns:
        Modified request with catalog context, or the original on failure.

    """
    user_msgs = [m for m in request.messages if m.role == "user"]
    if not user_msgs:
        return request

    latest = user_msgs[-1].content
    if len(latest) > 200 or latest.lower().startswith("welcome"):
        return request

    if len(latest.split()) <= 5:
        prior = " ".join(m.content for m in user_msgs[-4:-1])
        query = f"{prior} {latest}".strip() if prior else latest
    else:
        query = latest

    try:
        gender = _catalog_gender(request.customer_gender)

        # Heuristic shortcut: skip slow LLM intent extraction for outfit queries.
        is_outfit = _is_outfit_intent(query)
        if is_outfit:
            categories = ["tops", "bottoms"]
            intent_search_query = query
        else:
            intent = await _extract_shopping_intent(query, gender, None)
            categories = list(intent.overlay_categories)
            if intent.is_outfit_request and not categories:
                categories = ["tops", "bottoms"]
            intent_search_query = intent.search_query

        items: list[Any] = []

        # Try category-specific search first.
        if len(categories) > 1:
            per_cat = max(2, 4 // len(categories))
            seen: set[str] = set()
            for cat in categories:
                try:
                    cat_items = await adapter.search(
                        SearchRequest(
                            query=intent_search_query,
                            top_k=per_cat,
                            filters=CatalogQuery(
                                gender=gender, overlay_category=cat
                            ),
                        )
                    )
                    for it in cat_items:
                        if it.item_id not in seen:
                            seen.add(it.item_id)
                            items.append(it)
                except (ValueError, KeyError, AttributeError):
                    logging.warning(
                        "Category search failed for %s", cat, exc_info=True
                    )
        else:
            try:
                items = await adapter.search(
                    SearchRequest(
                        query=intent_search_query,
                        top_k=6,
                        filters=CatalogQuery(
                            gender=gender,
                            overlay_category=categories[0]
                            if categories
                            else None,
                        ),
                    )
                )
            except (ValueError, KeyError, AttributeError):
                logging.warning("Single-category search failed", exc_info=True)

        # Broader search without category filter.
        if not items:
            try:
                items = await adapter.search(
                    SearchRequest(
                        query=intent_search_query,
                        top_k=4,
                        filters=CatalogQuery(gender=gender),
                    )
                )
            except (ValueError, KeyError, AttributeError):
                logging.warning("Broad search failed", exc_info=True)

        # Final fallback: list all garments for gender.
        if not items:
            try:
                _fallback_limit = int(
                    os.environ.get("VTO_CATALOG_FALLBACK_LIMIT", "20")
                )
                fallback_all = await adapter.list_garments(
                    CatalogQuery(gender=gender, limit=_fallback_limit)
                )
                by_cat: dict[str, list] = {}
                for it in fallback_all:
                    by_cat.setdefault(it.overlay_category, []).append(it)
                catalog_items = []
                for cat_list in by_cat.values():
                    catalog_items.extend(cat_list[:2])
                items = [
                    GarmentSearchResult(
                        item_id=str(it.item_id),
                        name=it.name,
                        category=it.category,
                        overlay_category=it.overlay_category,
                        gender=it.gender,
                        image_path=it.image_path,
                        brand=it.brand,
                        color=it.color,
                        price=it.price,
                        score=1.0,
                    )
                    for it in catalog_items
                ]
            except (ValueError, KeyError, AttributeError):
                logging.warning("List garments fallback failed", exc_info=True)

        if not items:
            return request

        # When the user specified a color, float color-matched items to the
        # top so the agent's catalog_context leads with the relevant garment.
        # Semantic embeddings under-weight color vs. style tokens, so a
        # forest-green dress can rank below black/ivory items for "green dress".
        if _has_color_hint(query):
            _qcolors = {c for c in _COLOR_TERMS if c in query.lower()}
            items.sort(
                key=lambda it: 0
                if any(
                    c in (getattr(it, "color", "") or "").lower()
                    for c in _qcolors
                )
                else 1
            )

        lines = []
        for item in items:
            d = _dump_model(item)
            lines.append(
                f"id={d.get('item_id')} name={d.get('name')!r} "
                f"color={d.get('color')} cat={d.get('overlay_category')}"
            )
        catalog_block = (
            "\n\n<catalog_context>\n"
            + "\n".join(lines)
            + "\n</catalog_context>"
        )

        _garment_type_re = re.compile(
            r"\b(top|bottom|combo|dress|skirt|blouse|trouser|shirt|suit"
            r"|one.piece|gown|jeans|pants)\b",
            re.IGNORECASE,
        )
        _combo_re = re.compile(
            r"\b(top.and.bottom|combo|top\s+bottom|both)\b",
            re.IGNORECASE,
        )
        conversation_text = " ".join(
            m.content for m in request.messages if m.role == "user"
        )
        if _garment_type_re.search(conversation_text):
            is_combo = bool(_combo_re.search(conversation_text))
            if is_combo:
                catalog_block += (
                    "\n\nShopper wants a top + bottom combo. "
                    "Return ONLY raw JSON with EXACTLY 1 tops item AND 1 bottoms item:\n"
                    '{"recommendations":['
                    '{"item_id":"<tops-uuid>","overlay_category":"tops",'
                    '"recommendation_type":"primary","reasoning":"...","score":0.9},'
                    '{"item_id":"<bottoms-uuid>","overlay_category":"bottoms",'
                    '"recommendation_type":"complement","reasoning":"...","score":0.88}'
                    '],"styling_score":0.89,"suggestions":["..."]}'
                )
            else:
                catalog_block += (
                    "\n\nShopper has specified garment type. "
                    "Return ONLY raw JSON now — no prose, no questions:\n"
                    '{"recommendations":['
                    '{"item_id":"<uuid>","overlay_category":"tops",'
                    '"recommendation_type":"primary",'
                    '"reasoning":"...","score":0.9}'
                    '],"styling_score":0.88,"suggestions":["..."]}'
                )

        messages = list(request.messages)
        injected = False
        for i, msg in enumerate(messages):
            if msg.role == "system":
                messages[i] = ChatMessage(
                    role="system", content=msg.content + catalog_block
                )
                injected = True
                break
        if not injected:
            messages.insert(
                0, ChatMessage(role="system", content=catalog_block)
            )

        return AgentChatRequest(
            model=request.model,
            messages=messages,
            stream=request.stream,
            session_id=request.session_id,
            customer_gender=request.customer_gender,
            user=request.user,
        )
    except (ValueError, httpx.HTTPError, KeyError, AttributeError):
        logging.warning("Catalog context injection failed; forwarding request unchanged", exc_info=True)
        return request


_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

_CONTEXT_TAG_RE = re.compile(
    r"<(catalog|logistics)_context>.*?</(catalog|logistics)_context>",
    re.DOTALL | re.IGNORECASE,
)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_thinking_tags(text: str) -> str:
    """Strip Qwen thinking-mode tags that leak into agent output."""
    return _THINK_RE.sub("", text).strip()


async def _inject_logistics_context(
    request: AgentChatRequest,
    adapter: GarmentCatalogAdapter,
) -> AgentChatRequest:
    """Pre-fetch logistics data and inject it into the system message.

    Scans the last user message for a UUID item_id, fetches price and branch
    availability via the adapter, then appends a <logistics_context> block to
    the first system message so the logistics-agent formats a response without
    calling MCP tools.

    Returns the original request unchanged if no UUID is found or on any error.

    Args:
        request: Incoming agent chat request.
        adapter: Wired GarmentCatalogAdapter.

    Returns:
        Modified request with logistics context, or the original on failure.

    """
    user_msgs = [m for m in request.messages if m.role == "user"]
    if not user_msgs:
        return request

    query = user_msgs[-1].content
    match = _UUID_RE.search(query)
    item_id: str | None = None
    if match:
        item_id = match.group(0)
    else:
        try:
            item_id = await _resolve_recommended_item_id(
                adapter, request, query
            )
            if item_id is None:
                item_id = await _resolve_item_id_from_text(
                    adapter, query, _catalog_gender(request.customer_gender)
                )
        except (ValueError, AttributeError, KeyError):
            logging.exception(
                "Failed to resolve item name from user query text"
            )

    if item_id is None:
        return request

    try:
        logistics, item = await asyncio.gather(
            adapter.get_logistics(item_id),
            adapter.get_garment(item_id),
        )
        d = _dump_model(logistics)

        price = d.get("price") or d.get("display_price")
        currency = d.get("currency")
        if price is None and item is not None and item.price is not None:
            price = item.price
            currency = currency or "USD"

        branch_list = d.get("available_branches") or []
        branches = "; ".join(
            f"{b.get('branch_name') or b.get('city', '?')}:"
            f"{'in-stock' if (b.get('stock_on_hand') or 0) > 0 else 'OOS'}"
            for b in branch_list
        )
        avail = (
            branches if branches else d.get("availability_status", "unknown")
        )
        item_name = item.name if item else item_id
        logistics_block = (
            f"\n\n<logistics_context>\n"
            f"id={item_id} name={item_name!r}"
            f" price={price} currency={currency} branches=[{avail}]"
            f"\n</logistics_context>"
        )

        messages = list(request.messages)
        injected = False
        for i, msg in enumerate(messages):
            if msg.role == "system":
                messages[i] = ChatMessage(
                    role="system", content=msg.content + logistics_block
                )
                injected = True
                break
        if not injected:
            messages.insert(
                0, ChatMessage(role="system", content=logistics_block)
            )

        return AgentChatRequest(
            model=request.model,
            messages=messages,
            stream=request.stream,
            session_id=request.session_id,
            customer_gender=request.customer_gender,
            user=request.user,
        )
    except (ValueError, AttributeError, KeyError):
        logging.warning("Logistics context injection failed; forwarding request unchanged", exc_info=True)
        return request


async def _logistics_chat_response(
    request: AgentChatRequest,
    adapter: GarmentCatalogAdapter,
) -> dict[str, Any] | StreamingResponse:
    """Return logistics agent output without invoking OpenClaw.

    Logistics enrichment is deterministic because the API already owns the
    adapter call. Avoid sending strict JSON formatting to the LLM, which can
    fail OpenClaw's terminal response validation.

    Args:
        request: Incoming agent chat request.
        adapter: Wired GarmentCatalogAdapter.

    Returns:
        OpenAI-compatible chat response or stream.

    """
    user_msgs = [m for m in request.messages if m.role == "user"]
    query = user_msgs[-1].content if user_msgs else ""
    match = _UUID_RE.search(query)
    if not match:
        item_id = await _resolve_recommended_item_id(
            adapter,
            request,
            query,
        )
        if item_id is None:
            item_id = await _resolve_item_id_from_text(
                adapter,
                query,
                _catalog_gender(request.customer_gender),
            )
        if item_id is None:
            item_id = await _resolve_item_id_from_text(
                adapter,
                f"{_recent_user_text(request)} {query}",
                _catalog_gender(request.customer_gender),
            )
        if item_id is None:
            content = (
                "Which catalog item do you want pricing for? You can use the "
                "item name, such as Ruby Red Halter Top."
            )
        else:
            logistics = await adapter.get_logistics(item_id)
            item = await adapter.get_garment(item_id)
            content = _logistics_display_text(
                item.name if item else "That item",
                logistics,
                _requested_city(query),
            )
    else:
        logistics = await adapter.get_logistics(match.group(0))
        item = await adapter.get_garment(match.group(0))
        content = _logistics_display_text(
            item.name if item else "That item",
            logistics,
            _requested_city(query),
        )

    if request.stream:
        return StreamingResponse(
            _stream_chat_text(content),
            media_type="text/event-stream",
        )
    return _chat_text_response(content, request.model)


def _logistics_display_text(
    name: str,
    logistics: Any,
    requested_city: str | None = None,
) -> str:
    """Return shopper-friendly logistics text."""
    price = "price unavailable"
    if logistics.display_price is not None and logistics.currency:
        price = f"{logistics.display_price:.2f} {logistics.currency}"

    if requested_city:
        city_branch = next(
            (
                branch
                for branch in logistics.available_branches
                if branch.city.lower() == requested_city.lower()
            ),
            None,
        )
        if city_branch and (city_branch.available_to_promise or 0) > 0:
            return (
                f"{name}: {price}. It is available at "
                f"{city_branch.branch_name} in {city_branch.city}; "
                f"{city_branch.available_to_promise} available."
            )
        return (
            f"{name}: {price}. It is not available in the "
            f"{requested_city} branch."
        )

    branches = [
        branch
        for branch in logistics.available_branches
        if (branch.available_to_promise or 0) > 0
    ]
    if not branches:
        return (
            f"{name}: {price}. Availability is currently "
            f"{logistics.availability_status}."
        )

    branch = branches[0]
    return (
        f"{name}: {price}. Availability is {logistics.availability_status}; "
        f"{branch.branch_name} in {branch.city} has "
        f"{branch.available_to_promise} available."
    )


def _requested_city(text: str) -> str | None:
    """Return a branch city explicitly requested by the shopper."""
    lowered = text.lower()
    for city in ("atlanta", "chicago", "los angeles", "new york"):
        if city in lowered:
            return city.title()
    return None


async def _resolve_recommended_item_id(
    adapter: GarmentCatalogAdapter,
    request: AgentChatRequest,
    query: str,
) -> str | None:
    """Resolve short logistics questions against prior recommendations."""
    query_tokens = _lookup_tokens(query)
    state = _logistics_state(request)

    best_item_id = None
    best_score = 0
    item_ids = list(state.recommendation_item_ids)
    for item_id in await _display_recommendation_item_ids(adapter, request):
        if item_id not in item_ids:
            item_ids.append(item_id)
    if state.selected_item_id and state.selected_item_id not in item_ids:
        item_ids.insert(0, state.selected_item_id)

    garments = await adapter.get_garments(item_ids)
    for item_id in item_ids:
        item = garments.get(item_id)
        if item is None:
            continue
        item_tokens = _lookup_tokens(
            " ".join(
                value
                for value in (
                    item.name,
                    item.color,
                    item.category,
                    item.subcategory,
                )
                if value
            )
        )
        score = len(query_tokens & item_tokens)
        if score > best_score:
            best_item_id = item_id
            best_score = score

    if best_score >= 1:
        return best_item_id
    if state.selected_item_id and _is_contextual_item_query(query):
        return state.selected_item_id
    if len(item_ids) == 1 and _is_contextual_item_query(query):
        return item_ids[0]
    return None


def _logistics_state(request: AgentChatRequest) -> LogisticsConversationState:
    """Extract item context for logistics questions."""
    return LogisticsConversationState(
        selected_item_id=_selected_item_id(request),
        recommendation_item_ids=tuple(_recommendation_item_ids(request)),
    )


def _selected_item_id(request: AgentChatRequest) -> str | None:
    """Return the selected catalog item ID from the system message."""
    for message in request.messages:
        if message.role != "system":
            continue
        if "Selected catalog item:" not in message.content:
            continue
        match = _UUID_RE.search(message.content)
        if match:
            return match.group(0)
    return None


def _is_contextual_item_query(text: str) -> bool:
    """Return whether query can refer to the current item context."""
    lowered = text.lower()
    if not _is_logistics_intent(lowered):
        return False
    return any(
        term in lowered
        for term in (
            "it",
            "this",
            "that",
            "item",
            "dress",
            "top",
            "bottom",
            "price",
            "cost",
            "how much",
            "available",
            "availability",
            "branch",
            "stock",
        )
    )


async def _display_recommendation_item_ids(
    adapter: GarmentCatalogAdapter,
    request: AgentChatRequest,
) -> list[str]:
    """Resolve displayed recommendation names back to catalog IDs."""
    item_ids = []
    gender = _catalog_gender(request.customer_gender)
    items = await adapter.list_garments(CatalogQuery(gender=gender, limit=100))
    items_by_name = {item.name.lower(): item.item_id for item in items}
    for name in _display_recommendation_names(request):
        item_id = items_by_name.get(name.lower())
        if item_id:
            item_ids.append(item_id)
    return item_ids


def _display_recommendation_names(request: AgentChatRequest) -> list[str]:
    """Return item names from frontend-rendered recommendation messages."""
    names = []
    for message in request.messages:
        if message.role != "assistant":
            continue
        for line in message.content.splitlines():
            clean = line.strip().lstrip("-").strip()
            if ":" not in clean:
                continue
            name = clean.split(":", 1)[0].strip()
            if name and not name.lower().startswith("here are"):
                names.append(name)
    return names


def _has_recommendation_context(request: AgentChatRequest) -> bool:
    """Return whether recent assistant history contains recommendations."""
    return bool(
        _recommendation_item_ids(request)
        or _display_recommendation_names(request)
    )


def _recommendation_item_ids(request: AgentChatRequest) -> list[str]:
    """Return catalog item IDs from prior assistant recommendation payloads."""
    item_ids = []
    for message in request.messages:
        if message.role != "assistant":
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        recommendations = payload.get("recommendations", [])
        if not isinstance(recommendations, list):
            continue
        for recommendation in recommendations:
            item_id = recommendation.get("item_id")
            if isinstance(item_id, str):
                item_ids.append(item_id)
    return item_ids


async def _resolve_item_id_from_text(
    adapter: GarmentCatalogAdapter,
    text: str,
    gender: str | None,
) -> str | None:
    """Resolve a catalog item id from natural language text."""
    items = await adapter.list_garments(CatalogQuery(gender=gender, limit=100))
    query_tokens = _lookup_tokens(text)
    best_item = None
    best_score = 0
    for item in items:
        item_tokens = _lookup_tokens(
            " ".join(
                value
                for value in (
                    item.name,
                    item.color,
                    item.category,
                    item.subcategory,
                )
                if value
            )
        )
        score = len(query_tokens & item_tokens)
        if score > best_score:
            best_item = item
            best_score = score

    if best_item is not None and best_score >= 2:
        return best_item.item_id

    results = await adapter.search(
        SearchRequest(
            query=text,
            top_k=1,
            filters=CatalogQuery(gender=gender, limit=1),
        )
    )
    if not results:
        return None
    return results[0].item_id


def _lookup_tokens(text: str) -> set[str]:
    """Return useful lowercase lookup tokens."""
    stop_words = {
        "availability",
        "branch",
        "for",
        "item",
        "of",
        "price",
        "stock",
        "the",
        "this",
        "what",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in stop_words
    }


async def _stream_chat_text(content: str) -> AsyncIterator[bytes]:
    """Yield OpenAI-compatible SSE chunks for static text.

    Args:
        content: Text to stream.

    Yields:
        Encoded SSE chat completion chunks.

    """
    payload = {"choices": [{"delta": {"content": content}}]}
    yield f"data: {json.dumps(payload)}\n\n".encode()
    yield b"data: [DONE]\n\n"


def _chat_text_response(content: str, model: str) -> dict[str, Any]:
    """Build an OpenAI-compatible non-streaming chat response.

    Args:
        content: Assistant response text.
        model: Requested model id.

    Returns:
        Chat completion response dict.

    """
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ],
        "model": model,
    }


def _max_openclaw_turns() -> int:
    """Return max conversation turns forwarded to OpenClaw.

    Controlled by ``OPENCLAW_MAX_TURNS`` env var (default 5).
    """
    return int(os.environ.get("OPENCLAW_MAX_TURNS", "5"))


def _gateway_payload(request: AgentChatRequest) -> dict[str, Any]:
    """Build the OpenClaw Chat Completions payload.

    Caps history at _max_openclaw_turns() conversation messages (plus the
    system message) to prevent cli_budget exhaustion in OpenClaw.
    """
    system = [m for m in request.messages if m.role == "system"]
    convo = [m for m in request.messages if m.role != "system"]
    trimmed = system + convo[-_max_openclaw_turns():]
    payload: dict[str, Any] = {
        "model": _openclaw_model(request.model),
        "messages": [_dump_model(m) for m in trimmed],
        "stream": request.stream,
    }
    if request.stream:
        payload["stream_options"] = {"include_usage": True}

    session_user = request.user or request.session_id
    if session_user:
        payload["user"] = f"vto:{session_user}:{uuid.uuid4().hex}"

    max_tokens = os.environ.get("OPENCLAW_MAX_COMPLETION_TOKENS")
    if max_tokens:
        payload["max_completion_tokens"] = int(max_tokens)

    temperature = os.environ.get("OPENCLAW_TEMPERATURE")
    if temperature:
        payload["temperature"] = float(temperature)

    return payload


def _dump_model(model: BaseModel) -> dict[str, Any]:
    """Return a Pydantic model as a dict for v1/v2 compatibility."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _gateway_headers(request: AgentChatRequest) -> dict[str, str]:
    """Return headers required by OpenClaw Gateway auth and routing."""
    try:
        token = os.environ["OPENCLAW_GATEWAY_TOKEN"]
    except KeyError as exc:
        raise HTTPException(
            status_code=503, detail="OpenClaw token missing"
        ) from exc
    if not token:
        raise HTTPException(status_code=503, detail="OpenClaw token missing")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-openclaw-message-channel": "vto-dashboard",
    }
    agent_id = _agent_id(request.model)
    if agent_id:
        headers["x-openclaw-agent-id"] = agent_id

    backend_model = os.environ.get("OPENCLAW_BACKEND_MODEL", "")
    if backend_model:
        headers["x-openclaw-model"] = backend_model

    return headers


def _gateway_chat_url() -> str:
    """Return the configured OpenClaw Chat Completions URL."""
    base_url = os.environ.get("OPENCLAW_GATEWAY_URL", "http://openclaw:5789")
    return f"{base_url.rstrip('/')}/v1/chat/completions"


def _gateway_timeout() -> httpx.Timeout:
    """Return the HTTP timeout for OpenClaw calls."""
    timeout_seconds = float(os.environ.get("OPENCLAW_GATEWAY_TIMEOUT", "60"))
    return httpx.Timeout(timeout_seconds)


def _intent_timeout() -> float:
    """Return the timeout (seconds) for the LLM intent-extraction call.

    A short timeout is used because this is a blocking pre-flight call that
    must complete before the main OpenClaw request is sent.  If the local
    LLM is warm the call usually finishes well under this budget; if not,
    we fall back to fast rule-based heuristics instantly.
    """
    return float(os.environ.get("OPENCLAW_INTENT_TIMEOUT", "10"))


def _openclaw_model(model: str) -> str:
    """Translate VTO agent ids into OpenClaw agent-target model ids."""
    if model in ("", "openclaw", "openclaw/default"):
        return "openclaw/default"

    agent_id = _agent_id(model)
    if agent_id not in _OPENCLAW_AGENT_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown OpenClaw agent {agent_id!r}",
        )
    return f"openclaw/{agent_id}"


def _agent_id(model: str) -> str | None:
    """Return the VTO OpenClaw agent id encoded by a model value."""
    if model in ("", "openclaw", "openclaw/default"):
        return None
    return model.removeprefix("openclaw/")


def _strip_context_tags(text: str) -> str:
    """Strip injected context blocks that leaked into agent output.

    Args:
        text: Raw agent response text.

    Returns:
        Text with catalog_context and logistics_context tags removed.

    """
    return _CONTEXT_TAG_RE.sub("", text).strip()


async def _deterministic_fallback(
    request: AgentChatRequest,
    adapter: GarmentCatalogAdapter,
    is_logistics: bool,
) -> dict[str, Any] | StreamingResponse:
    """Return catalog-grounded response when OpenClaw errors or returns bad data.

    Args:
        request: Original (unenriched) agent chat request.
        adapter: Wired GarmentCatalogAdapter.
        is_logistics: True routes to logistics helper; False to styling helper.

    Returns:
        OpenAI-compatible response dict or StreamingResponse.

    """
    if is_logistics:
        return await _logistics_chat_response(request, adapter)
    return await _fashion_stylist_chat_response(request, adapter)


async def _validate_openclaw_response(
    data: dict[str, Any],
    request: AgentChatRequest,
    adapter: GarmentCatalogAdapter,
    is_logistics: bool,
) -> dict[str, Any] | StreamingResponse:
    """Validate and clean a non-streaming OpenClaw response.

    Strips leaked context tags, validates recommendation item IDs against the
    catalog, and falls back to the deterministic path on empty or invalid output.

    Args:
        data: Raw JSON response dict from OpenClaw.
        request: Original (unenriched) agent chat request.
        adapter: Wired GarmentCatalogAdapter.
        is_logistics: Whether to use the logistics fallback path.

    Returns:
        Cleaned response dict, or a deterministic fallback.

    """
    choices = data.get("choices", [])
    if not choices:
        return await _deterministic_fallback(request, adapter, is_logistics)

    choice = choices[0]
    content = (
        (choice.get("message") or {}).get("content")
        or (choice.get("delta") or {}).get("content")
        or ""
    )

    if not content.strip():
        return await _deterministic_fallback(request, adapter, is_logistics)

    content = _strip_thinking_tags(_strip_context_tags(content))

    try:
        rec_payload = json.loads(content)
        recommendations = rec_payload.get("recommendations")
        if isinstance(recommendations, list):
            # Batch-fetch every referenced garment in one query (avoids N+1).
            candidate_ids = [
                rec if isinstance(rec, str) else rec.get("item_id")
                for rec in recommendations
            ]
            garments = await adapter.get_garments(
                [cid for cid in candidate_ids if isinstance(cid, str)]
            )
            normalized = []
            for rec in recommendations:
                if isinstance(rec, str):
                    item = garments.get(rec)
                    if item is None:
                        return await _deterministic_fallback(
                            request, adapter, is_logistics
                        )
                    normalized.append(
                        {
                            "item_id": rec,
                            "overlay_category": item.overlay_category,
                            "recommendation_type": "primary",
                            "reasoning": item.name,
                            "score": 0.8,
                        }
                    )
                else:
                    item_id = rec.get("item_id")
                    if item_id and garments.get(item_id) is None:
                        return await _deterministic_fallback(
                            request, adapter, is_logistics
                        )
                    normalized.append(rec)
            if normalized != recommendations:
                rec_payload["recommendations"] = normalized
                content = json.dumps(rec_payload)
    except (json.JSONDecodeError, AttributeError):
        pass

    cleaned_choice = dict(choice)
    if "message" in cleaned_choice:
        cleaned_choice["message"] = {
            **cleaned_choice["message"],
            "content": content,
        }
    elif "delta" in cleaned_choice:
        cleaned_choice["delta"] = {
            **cleaned_choice["delta"],
            "content": content,
        }
    return {**data, "choices": [cleaned_choice, *choices[1:]]}


async def _get_fallback_content(
    request: AgentChatRequest,
    adapter: GarmentCatalogAdapter,
    is_logistics: bool,
) -> str:
    """Compute deterministic fallback content string for streaming error recovery.

    Args:
        request: Original agent chat request.
        adapter: Wired GarmentCatalogAdapter.
        is_logistics: Whether to compute logistics vs styling content.

    Returns:
        Plain-text or JSON content string, or empty string on failure.

    """
    non_stream = AgentChatRequest(
        model=request.model,
        messages=request.messages,
        stream=False,
        session_id=request.session_id,
        customer_gender=request.customer_gender,
        user=request.user,
    )
    if is_logistics:
        result = await _logistics_chat_response(non_stream, adapter)
    else:
        result = await _fashion_stylist_chat_response(non_stream, adapter)
    if isinstance(result, dict):
        choices = result.get("choices", [])
        if choices:
            return (choices[0].get("message") or {}).get("content", "")
    return ""


def _sse_has_error(line: str) -> bool:
    """Return True if an SSE data line carries an error payload.

    Args:
        line: A single SSE line starting with "data: ".

    Returns:
        True when the parsed JSON contains a top-level "error" key.

    """
    if not line.startswith("data: "):
        return False
    payload = line[6:].strip()
    if payload in ("[DONE]", ""):
        return False
    try:
        parsed = json.loads(payload)
        return bool(parsed.get("error"))
    except (json.JSONDecodeError, AttributeError):
        return False


async def _stream_openclaw_with_fallback(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    request: AgentChatRequest,
    adapter: GarmentCatalogAdapter,
    is_logistics: bool,
) -> AsyncIterator[bytes]:
    """Stream OpenClaw with deterministic fallback on HTTP, connection, or error.

    Inspects each SSE line; if an error chunk is received (e.g. cli_budget
    exceeded), discards accumulated output and yields the deterministic fallback
    instead so the frontend never sees an empty or error response.

    Args:
        url: OpenClaw Chat Completions endpoint URL.
        headers: Gateway auth and routing headers.
        payload: OpenAI-compatible request payload.
        request: Original (unenriched) agent chat request.
        adapter: Wired GarmentCatalogAdapter.
        is_logistics: Whether to fall back to logistics vs styling helper.

    Yields:
        Encoded SSE bytes from OpenClaw, or fallback content on error.

    """
    use_fallback = False
    yielded_anything = False
    try:
        _stream_timeout = float(
            os.environ.get("OPENCLAW_STREAM_TIMEOUT", "50")
        )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_stream_timeout)
        ) as client:
            async with client.stream(
                "POST", url, headers=headers, json=payload
            ) as response:
                if response.status_code >= 400:
                    use_fallback = True
                else:
                    buf = b""
                    async for chunk in response.aiter_bytes():
                        buf += chunk
                        while b"\n" in buf:
                            line_bytes, buf = buf.split(b"\n", 1)
                            line = line_bytes.decode("utf-8", errors="replace")
                            if _sse_has_error(line):
                                use_fallback = True
                                break
                            yield (line + "\n").encode()
                            yielded_anything = True
                        if use_fallback:
                            break
                    else:
                        if buf:
                            yield buf
                            yielded_anything = True
    except httpx.RequestError:
        use_fallback = True

    if use_fallback or not yielded_anything:
        fallback = await _get_fallback_content(request, adapter, is_logistics)
        async for chunk in _stream_chat_text(fallback):
            yield chunk
