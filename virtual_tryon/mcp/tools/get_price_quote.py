# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""get_price_quote MCP tool — display price and discount for a catalog item."""

from __future__ import annotations

from typing import Any

import asyncpg


async def run(db: asyncpg.Pool, item_id: str) -> dict[str, Any]:
    """Return the best price quote for a catalog item.

    Picks the row with the highest-priority availability status
    (in_stock > low_stock > out_of_stock > unknown).

    Args:
        db: asyncpg connection pool.
        item_id: UUID string of the catalog item.

    Returns:
        Dict with keys: item_id, display_price, currency, discount_label,
        availability_status.

    """
    rows = await db.fetch(
        """
        SELECT
            bi.display_price::float,
            bi.currency,
            bi.availability_status
        FROM vto_branch_inventory bi
        WHERE bi.item_id = $1::uuid
        ORDER BY
            CASE bi.availability_status
                WHEN 'in_stock' THEN 0
                WHEN 'low_stock' THEN 1
                WHEN 'out_of_stock' THEN 2
                ELSE 3
            END
        LIMIT 1
        """,
        item_id,
    )

    if not rows:
        # Fall back to catalog list price if no inventory row exists
        catalog = await db.fetchrow(
            "SELECT price::float FROM vto_catalog WHERE item_id=$1::uuid",
            item_id,
        )
        return {
            "item_id": item_id,
            "display_price": (
                float(catalog["price"])
                if catalog and catalog["price"]
                else None
            ),
            "currency": "INR",
            "discount_label": None,
            "availability_status": "unknown",
        }

    row = rows[0]
    return {
        "item_id": item_id,
        "display_price": row["display_price"],
        "currency": row["currency"] or "INR",
        "discount_label": None,
        "availability_status": row["availability_status"] or "unknown",
    }
