# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Run DB migrations and seed catalog from dataset.json.

Idempotent: safe to run on every container start.
- Migrations use IF NOT EXISTS / DO NOTHING guards.
- Seeding skipped if vto_catalog already has rows.

Usage::
    python virtual_tryon/catalog/init_db.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import asyncpg

_ROOT = (
    Path(__file__).resolve().parents[2]
)  # catalog/ -> virtual_tryon/ -> repo root
_MIGRATIONS = [
    _ROOT / "virtual_tryon" / "storage" / "migrations" / "001_catalog.sql",
    _ROOT / "virtual_tryon" / "storage" / "migrations" / "002_runtime.sql",
    _ROOT / "virtual_tryon" / "storage" / "migrations" / "003_gender.sql",
    _ROOT / "virtual_tryon" / "storage" / "migrations" / "004_feedback.sql",
    _ROOT / "virtual_tryon" / "storage" / "migrations" / "005_constraints.sql",
    _ROOT / "virtual_tryon" / "storage" / "migrations" / "006_remove_camera_active.sql",
]
_DATASET = _ROOT / "catalogue" / "dataset.json"
_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

_CATEGORY_MAP = {
    "tshirt": ("tops", "t-shirt", "tops"),
    "shirt": ("tops", "shirt", "tops"),
    "pants_shorts": ("bottoms", "pants_shorts", "bottoms"),
    "top": ("tops", "top", "tops"),
    "maxi_dress": ("one-pieces", "maxi_dress", "one-pieces"),
}


async def run_migrations(conn: asyncpg.Connection) -> None:
    for path in _MIGRATIONS:
        sql = path.read_text()
        await conn.execute(sql)
        print(f"  migration ok: {path.name}")


async def seed_catalog(conn: asyncpg.Connection) -> None:
    data = json.loads(_DATASET.read_text())

    branch_uuid_map: dict[str, uuid.UUID] = {}
    for b in data["branches"]:
        uid = uuid.uuid5(_NAMESPACE, b["branch_id"])
        branch_uuid_map[b["branch_id"]] = uid
        meta = json.dumps(
            {"original_branch_id": b["branch_id"], "state": b["state"]}
        )
        await conn.execute(
            """
            INSERT INTO vto_branches (branch_id, name, city, address, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (branch_id) DO NOTHING
            """,
            uid,
            b["branch_name"],
            b["city"],
            b["address"],
            meta,
        )

    for g in data["garments"]:
        item_uid = uuid.uuid5(_NAMESPACE, str(g["garment_id"]))
        cat = g["category"]
        category, subcategory, overlay = _CATEGORY_MAP.get(cat, (cat, cat, cat))
        meta = json.dumps(
            {"garment_id": g["garment_id"], "collection": g["collection"]}
        )
        await conn.execute(
            """
            INSERT INTO vto_catalog (
                item_id, name, category, subcategory, overlay_category,
                gender, color, size_range, price, description, image_path, metadata
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
            ON CONFLICT (item_id) DO UPDATE SET
                image_path = EXCLUDED.image_path,
                updated_at = NOW()
            """,
            item_uid,
            g["name"],
            category,
            subcategory,
            overlay,
            g["gender"],
            g["color"],
            g["sizes_available"],
            g["price_usd"],
            g["description"],
            g["image_path"],
            meta,
        )

        for inv in g.get("available_at", []):
            branch_uid = branch_uuid_map.get(inv["branch_id"])
            if not branch_uid:
                continue
            status = "in_stock" if inv["in_stock"] else "out_of_stock"
            await conn.execute(
                """
                INSERT INTO vto_branch_inventory (
                    item_id, branch_id, stock_on_hand, available_to_promise,
                    display_price, currency, availability_status
                ) VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (item_id, branch_id) DO NOTHING
                """,
                item_uid,
                branch_uid,
                inv["stock_on_hand"],
                inv.get("available_to_promise"),
                inv["display_price_usd"],
                "USD",
                status,
            )

    garment_count = await conn.fetchval("SELECT COUNT(*) FROM vto_catalog")
    branch_count = await conn.fetchval("SELECT COUNT(*) FROM vto_branches")
    inv_count = await conn.fetchval("SELECT COUNT(*) FROM vto_branch_inventory")
    print(
        f"  seeded: {garment_count} garments, {branch_count} branches, {inv_count} inventory rows"
    )


async def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    print("init_db: connecting to postgres...")
    for attempt in range(1, 11):
        try:
            conn = await asyncpg.connect(db_url)
            break
        except Exception as exc:
            print(f"  attempt {attempt}/10 failed: {exc}")
            if attempt == 10:
                print("init_db: could not connect — aborting")
                sys.exit(1)
            await asyncio.sleep(3)

    try:
        print("init_db: running migrations...")
        await run_migrations(conn)
        print("init_db: seeding catalog...")
        await seed_catalog(conn)
    finally:
        await conn.close()

    print("init_db: done")


if __name__ == "__main__":
    asyncio.run(main())
