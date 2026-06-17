# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""query_pgvector MCP tool — predefined catalog and logistics DB queries."""

from __future__ import annotations

from typing import Any

import asyncpg

# Predefined safe queries indexed by name.
# OpenClaw agents call this tool by name + params; raw SQL never flows through.
_QUERIES: dict[str, str] = {
    "catalog_list": """
        SELECT
            item_id::text, name, category, subcategory, overlay_category,
            brand, color, price::float
        FROM vto_catalog
        WHERE
            ($1::text IS NULL OR category = $1)
            AND ($2::text IS NULL OR overlay_category = $2)
            AND ($3::text IS NULL OR color = $3)
            AND ($4::float IS NULL OR price >= $4)
            AND ($5::float IS NULL OR price <= $5)
        ORDER BY name
        LIMIT $6 OFFSET $7
    """,
    "catalog_get": """
        SELECT
            item_id::text, name, category, subcategory, overlay_category,
            brand, color, price::float
        FROM vto_catalog
        WHERE item_id = $1::uuid
    """,
    "catalog_count": """
        SELECT COUNT(*) AS total FROM vto_catalog
        WHERE ($1::text IS NULL OR category = $1)
    """,
    "logistics_availability": """
        SELECT
            bi.display_price::float,
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
        WHERE bi.item_id = $1::uuid
        ORDER BY
            CASE bi.availability_status
                WHEN 'in_stock' THEN 0
                WHEN 'low_stock' THEN 1
                WHEN 'out_of_stock' THEN 2
                ELSE 3
            END
    """,
    "tryon_history": """
        SELECT
            th.tryon_id::text, th.session_id::text, th.garment_id::text,
            th.created_at::text, th.rating, th.feedback,
            c.name AS garment_name, c.image_path
        FROM vto_tryon_history th
        JOIN vto_catalog c ON c.item_id = th.garment_id
        WHERE th.session_id = $1::uuid
        ORDER BY th.created_at DESC
        LIMIT $2
    """,
    "styling_recommendations_get": """
        SELECT
            rec_id::text, session_id::text, base_garment_id::text,
            recommended_garment_id::text, recommendation_type, score::float,
            reasoning, created_at::text
        FROM vto_styling_recommendations
        WHERE session_id = $1::uuid
        ORDER BY score DESC
        LIMIT $2
    """,
    "feedback_summary": """
        SELECT
            COUNT(*) FILTER (WHERE overlay_quality_rating <= 2) AS negative_overlay,
            COUNT(*) FILTER (WHERE fit_rating <= 2) AS negative_fit,
            COUNT(*) FILTER (WHERE logistics_usefulness_rating <= 2)
                AS negative_logistics,
            COUNT(*) FILTER (WHERE availability_accuracy_rating <= 2)
                AS negative_availability,
            COUNT(*) FILTER (WHERE shopping_experience_rating <= 2)
                AS negative_experience,
            COUNT(*) AS total,
            (ARRAY_REMOVE(ARRAY_AGG(feedback ORDER BY created_at DESC), NULL))[1:5]
                AS feedback_samples
        FROM vto_customer_feedback
        WHERE garment_id = $1::uuid
    """,
}


async def run(
    db: asyncpg.Pool,
    query_name: str,
    params: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute a named predefined query and return rows as dicts.

    Args:
        db: asyncpg connection pool.
        query_name: Key into _QUERIES.
        params: Positional parameters for the query.

    Returns:
        List of row dicts.

    Raises:
        ValueError: If query_name not in _QUERIES.
        asyncpg.PostgresError: On DB errors.

    """
    if query_name not in _QUERIES:
        raise ValueError(
            f"Unknown query_name {query_name!r}. "
            f"Available: {sorted(_QUERIES)}"
        )
    sql = _QUERIES[query_name]
    rows = await db.fetch(sql, *(params or []))
    return [dict(r) for r in rows]
