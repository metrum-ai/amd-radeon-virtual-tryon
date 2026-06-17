# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""VTO MCP tool server — exposes catalog and logistics tools over HTTP.

Start with:
    python -m virtual_tryon.mcp.server

Environment variables:
    DATABASE_URL      asyncpg-compatible PostgreSQL URL (required)
    REDIS_URL         Valkey/Redis-compatible URL (default: redis://valkey:6379)
    MCP_HOST          bind host (default: '0.0.0.0')
    MCP_PORT          bind port (default: 8001)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import redis.asyncio as aioredis
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from virtual_tryon.mcp.tools import (
    get_branch_availability,
    get_price_quote,
    publish_event,
    query_pgvector,
    search_milvus_rag,
)

# --------------------------------------------------------------------------- #
# Server instance
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def _lifespan(server: FastMCP):
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


mcp = FastMCP(
    "vto-mcp",
    instructions=(
        "VTO catalog and logistics tool server. "
        "Use query_pgvector for structured catalog/history queries. "
        "Use get_branch_availability and get_price_quote for logistics data. "
        "Use search_milvus_rag for semantic catalog search. "
        "Use publish_event to emit workflow events to the VTO event bus."
    ),
    lifespan=_lifespan,
)

# Module-level singletons initialised at startup
_db: asyncpg.Pool | None = None
_redis: aioredis.Redis | None = None


# --------------------------------------------------------------------------- #
# Health endpoint
# --------------------------------------------------------------------------- #


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    """Liveness probe — returns 200 when the MCP server is running.

    Returns:
        JSON status dict.

    """
    return JSONResponse({"status": "ok"})


# --------------------------------------------------------------------------- #
# Tool definitions
# --------------------------------------------------------------------------- #


@mcp.tool()
async def query_pgvector_tool(
    query_name: str,
    params: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute a named predefined catalog or history DB query.

    Available query_name values:
    - catalog_list: list garments with optional filters
      params: [category, overlay_category, color, min_price, max_price, limit, offset]
    - catalog_get: fetch single garment by item_id
      params: [item_id]
    - logistics_availability: branch inventory for item_id
      params: [item_id]
    - tryon_history: recent try-on history for session_id
      params: [session_id, limit]
    - styling_recommendations_get: saved recommendations for session_id
      params: [session_id, limit]
    - feedback_summary: negative feedback summary for garment_id
      params: [garment_id]

    Args:
        query_name: Name of the predefined query to run.
        params: Ordered positional params list (use null for optional filters).

    Returns:
        List of result rows as dicts.

    """
    if _db is None:
        raise RuntimeError("Database not initialised")
    return await query_pgvector.run(_db, query_name, params)


@mcp.tool()
async def get_branch_availability_tool(item_id: str) -> dict[str, Any]:
    """Return branch-level stock and availability for a catalog garment.

    Args:
        item_id: Garment UUID string.

    Returns:
        Dict with availability_status, available_branches, preferred_branch_id.

    """
    if _db is None:
        raise RuntimeError("Database not initialised")
    return await get_branch_availability.run(_db, item_id)


@mcp.tool()
async def get_price_quote_tool(item_id: str) -> dict[str, Any]:
    """Return the best display price and currency for a catalog garment.

    Args:
        item_id: Garment UUID string.

    Returns:
        Dict with display_price, currency, discount_label, availability_status.

    """
    if _db is None:
        raise RuntimeError("Database not initialised")
    return await get_price_quote.run(_db, item_id)


@mcp.tool()
async def publish_event_tool(
    event_type: str,
    payload: dict[str, Any],
    channel: str = "vto:events",
) -> dict[str, Any]:
    """Publish a VTO workflow event to the Redis event bus.

    Allowed event_type values:
    - session.created
    - recommendations.ready
    - garment.select
    - overlay.updated
    - overlay.snapshot_saved
    - feedback.submitted

    Args:
        event_type: Event identifier string.
        payload: JSON-serialisable event data (max 256 KiB).
        channel: Redis channel (default 'vto:events'). Only 'vto:events' is permitted.

    Returns:
        Dict with event_type, channel, published_at, subscriber_count.

    Raises:
        ValueError: If channel, event_type, or payload size is invalid.

    """
    if _redis is None:
        raise RuntimeError("Redis not initialised")
    return await publish_event.run(_redis, event_type, payload, channel)


@mcp.tool()
async def search_milvus_rag_tool(
    query: str,
    top_k: int = 10,
    category_filter: str | None = None,
    overlay_category_filter: str | None = None,
    gender_filter: str | None = None,
    max_price: float | None = None,
) -> list[dict[str, Any]]:
    """Semantic catalog search. Falls back to PG ILIKE when Milvus is disabled.

    Args:
        query: Natural language query (e.g. 'casual summer blue top').
        top_k: Max results (default 10).
        category_filter: Optional category name filter.
        overlay_category_filter: Optional overlay zone filter (tops/bottoms/one-pieces).
        gender_filter: Optional gender filter ('women'|'men'|'unisex'). Includes unisex
            items for any gender-specific filter.
        max_price: Optional price ceiling. Items above this are excluded.

    Returns:
        List of garment dicts with item_id, name, category, overlay_category,
        brand, color, price, image_path, score.

    """
    if _db is None:
        raise RuntimeError("Database not initialised")
    return await search_milvus_rag.run(
        _db,
        query,
        top_k,
        category_filter,
        overlay_category_filter,
        gender_filter,
        max_price,
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


async def _startup() -> None:
    global _db, _redis
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL is required but not set. "
            "Configure it in your environment or .env file."
        )
    redis_url = os.environ.get("REDIS_URL", "redis://valkey:6379")
    _db = await asyncpg.create_pool(db_url, min_size=2, max_size=10)
    _redis = aioredis.from_url(redis_url, decode_responses=True)


async def _shutdown() -> None:
    if _db:
        await _db.close()
    if _redis:
        await _redis.aclose()


if __name__ == "__main__":
    host = os.environ.get("MCP_HOST")
    port = int(os.environ.get("MCP_PORT", "8001"))
    mcp.run(transport="streamable-http", host=host, port=port)
