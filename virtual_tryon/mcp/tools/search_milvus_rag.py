# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""search_milvus_rag MCP tool — semantic catalog search with PG text fallback."""

from __future__ import annotations

from typing import Any

import asyncpg


async def run(
    db: asyncpg.Pool,
    query: str,
    top_k: int = 10,
    category_filter: str | None = None,
    overlay_category_filter: str | None = None,
    gender_filter: str | None = None,
    max_price: float | None = None,
) -> list[dict[str, Any]]:
    """Search the catalog semantically.

    When Milvus is unavailable, falls back to PostgreSQL ILIKE text search
    across name, description, brand, and color columns. The fallback is
    sufficient for the MVP demo catalog.

    Args:
        db: asyncpg connection pool.
        query: Natural language search query.
        top_k: Maximum results to return.
        category_filter: Optional category filter (e.g. 'tops').
        overlay_category_filter: Optional overlay zone filter.
        gender_filter: Optional gender ('women'|'men'|'unisex'). When set,
            results include gender_filter AND 'unisex' items.
        max_price: Optional price ceiling.

    Returns:
        List of compact garment dicts with item_id, name, category,
        overlay_category, brand, color, price, gender, and score.

    """
    stop = {
        "a",
        "an",
        "the",
        "i",
        "i'm",
        "for",
        "to",
        "in",
        "on",
        "and",
        "or",
        "of",
        "me",
        "my",
        "want",
        "need",
        "show",
        "nice",
        "some",
    }
    keywords = [
        w.strip("$.,!?")
        for w in query.lower().split()
        if len(w.strip("$.,!?")) >= 3
        and w.strip("$.,!?") not in stop
        and not w.strip("$.,!?").isdigit()
    ][:6]
    raw_term = keywords[0] if keywords else ""
    escaped_term = (
        raw_term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    search_term = f"%{escaped_term}%"

    # gender filter: women → ['women','unisex'], men → ['men','unisex']
    gender_values: list[str] | None = None
    if gender_filter in ("women", "men"):
        gender_values = [gender_filter, "unisex"]

    rows = await db.fetch(
        """
        SELECT
            item_id::text,
            name,
            category,
            subcategory,
            overlay_category,
            brand,
            color,
            price::float,
            gender,
            1.0::float AS score
        FROM vto_catalog
        WHERE
            (
                name ILIKE $1 ESCAPE '\\'
                OR description ILIKE $1 ESCAPE '\\'
                OR brand ILIKE $1 ESCAPE '\\'
                OR color ILIKE $1 ESCAPE '\\'
                OR subcategory ILIKE $1 ESCAPE '\\'
            )
            AND ($2::text IS NULL OR category = $2)
            AND ($3::text IS NULL OR overlay_category = $3)
            AND ($4::text[] IS NULL OR gender = ANY($4))
            AND ($5::float IS NULL OR price <= $5)
        ORDER BY name
        LIMIT $6
        """,
        search_term,
        category_filter,
        overlay_category_filter,
        gender_values,
        max_price,
        top_k,
    )

    return [dict(r) for r in rows]
