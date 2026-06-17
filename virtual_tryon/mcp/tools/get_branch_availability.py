# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""get_branch_availability MCP tool — branch stock and ATP for a catalog item."""

from __future__ import annotations

from typing import Any

import asyncpg


async def run(db: asyncpg.Pool, item_id: str) -> dict[str, Any]:
    """Return branch-level availability for a catalog item.

    Args:
        db: asyncpg connection pool.
        item_id: UUID string of the catalog item.

    Returns:
        Dict with keys: item_id, availability_status, available_branches,
        preferred_branch_id.

    """
    rows = await db.fetch(
        """
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
        item_id,
    )

    if not rows:
        return {
            "item_id": item_id,
            "availability_status": "unknown",
            "available_branches": [],
            "preferred_branch_id": None,
        }

    _priority = {"in_stock": 0, "low_stock": 1, "out_of_stock": 2, "unknown": 3}
    branches = []
    best_status = "unknown"
    for row in rows:
        status = row["availability_status"] or "unknown"
        if _priority.get(status, 3) < _priority.get(best_status, 3):
            best_status = status
        branches.append(
            {
                "branch_id": row["branch_id"],
                "branch_name": row["branch_name"],
                "city": row["city"],
                "stock_on_hand": row["stock_on_hand"],
                "available_to_promise": row["available_to_promise"],
                "pickup_eta_minutes": row["pickup_eta_minutes"],
                "fulfillment_options": list(row["fulfillment_options"] or []),
            }
        )

    return {
        "item_id": item_id,
        "availability_status": best_status,
        "available_branches": branches,
        "preferred_branch_id": branches[0]["branch_id"] if branches else None,
    }
