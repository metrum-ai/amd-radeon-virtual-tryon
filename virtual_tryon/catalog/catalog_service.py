# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Catalog CRUD service backed by PostgreSQL."""

from __future__ import annotations

import json
import uuid

from virtual_tryon.storage.postgres import PostgresPool
from virtual_tryon.storage.schemas import CatalogQuery, GarmentItem


class CatalogService:
    """CRUD operations on the ``vto_catalog`` table.

    Args:
        db: Connected PostgresPool instance.

    """

    def __init__(self, db: PostgresPool) -> None:
        """Initialize the CatalogService."""
        self._db = db

    async def insert_garment(self, item: GarmentItem) -> str:
        """Insert a garment and return the generated item_id.

        Args:
            item: GarmentItem to persist.

        Returns:
            UUID string of the new row.

        """
        item_id = item.item_id or str(uuid.uuid4())
        metadata_json = json.dumps(item.metadata) if item.metadata else None
        await self._db.execute(
            """
            INSERT INTO vto_catalog (
                item_id, name, category, subcategory, overlay_category,
                gender, brand, color, size_range, price, description,
                image_path, embedding_id, logistics_ref, branch_inventory_ref,
                metadata
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb)
            ON CONFLICT (item_id) DO UPDATE SET
                name=EXCLUDED.name,
                category=EXCLUDED.category,
                subcategory=EXCLUDED.subcategory,
                overlay_category=EXCLUDED.overlay_category,
                gender=EXCLUDED.gender,
                brand=EXCLUDED.brand,
                color=EXCLUDED.color,
                size_range=EXCLUDED.size_range,
                price=EXCLUDED.price,
                description=EXCLUDED.description,
                image_path=EXCLUDED.image_path,
                embedding_id=EXCLUDED.embedding_id,
                logistics_ref=EXCLUDED.logistics_ref,
                branch_inventory_ref=EXCLUDED.branch_inventory_ref,
                metadata=EXCLUDED.metadata,
                updated_at=NOW()
            """,
            item_id,
            item.name,
            item.category,
            item.subcategory,
            item.overlay_category,
            item.gender,
            item.brand,
            item.color,
            item.size_range,
            item.price,
            item.description,
            item.image_path,
            item.embedding_id,
            item.logistics_ref,
            item.branch_inventory_ref,
            metadata_json,
        )
        return item_id

    async def get_garment(self, item_id: str) -> GarmentItem | None:
        """Fetch a single garment by ID.

        Args:
            item_id: UUID string.

        Returns:
            GarmentItem or None if not found.

        """
        row = await self._db.fetchrow(
            "SELECT * FROM vto_catalog WHERE item_id=$1", item_id
        )
        if not row:
            return None
        return _row_to_item(row)

    async def get_garments(
        self, item_ids: list[str]
    ) -> dict[str, GarmentItem]:
        """Fetch multiple garments by ID in a single query.

        Args:
            item_ids: UUID strings. Non-UUID or unknown IDs are omitted from
                the result rather than raising.

        Returns:
            Mapping of item_id string to GarmentItem for every ID that exists.

        """
        valid_ids: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for raw in item_ids:
            try:
                parsed = uuid.UUID(str(raw))
            except (ValueError, AttributeError, TypeError):
                continue
            if parsed not in seen:
                seen.add(parsed)
                valid_ids.append(parsed)
        if not valid_ids:
            return {}
        rows = await self._db.fetch(
            "SELECT * FROM vto_catalog WHERE item_id = ANY($1::uuid[])",
            valid_ids,
        )
        return {str(r["item_id"]): _row_to_item(r) for r in rows}

    async def list_garments(
        self, query: CatalogQuery, text_query: str | None = None
    ) -> list[GarmentItem]:
        """List garments with optional filters.

        Args:
            query: CatalogQuery filter parameters.

        Returns:
            List of matching GarmentItems.

        """
        conditions: list[str] = []
        params: list[object] = []
        idx = 1

        for field, col in (
            ("category", "category"),
            ("subcategory", "subcategory"),
            ("overlay_category", "overlay_category"),
            ("brand", "brand"),
        ):
            value = getattr(query, field)
            if value is not None:
                conditions.append(f"{col}=${idx}")
                params.append(value)
                idx += 1

        # Partial ILIKE so "blue" matches "cobalt blue", "powder blue", "sky blue"
        if query.color is not None:
            conditions.append(f"color ILIKE ${idx}")
            params.append(f"%{query.color}%")
            idx += 1

        # Include unisex items when filtering by gender (women → women+unisex, men → men+unisex)
        if query.gender is not None:
            conditions.append(f"gender = ANY(${idx})")
            params.append([query.gender, "unisex"])
            idx += 1

        if query.min_price is not None:
            conditions.append(f"price>=${idx}")
            params.append(query.min_price)
            idx += 1
        if query.max_price is not None:
            conditions.append(f"price<=${idx}")
            params.append(query.max_price)
            idx += 1

        if text_query:
            # Use individual-word OR search (not conjunction) so "blue casual dress"
            # matches any of those words rather than requiring all in sequence.
            stop = {
                "a",
                "an",
                "the",
                "i",
                "i'm",
                "am",
                "is",
                "for",
                "to",
                "in",
                "on",
                "and",
                "or",
                "of",
                "me",
                "my",
                "do",
                "have",
                "want",
                "need",
                "looking",
                "show",
                "something",
                "nice",
                "please",
                "would",
                "like",
                "some",
                "under",
                "budget",
            }
            keywords = [
                w.strip("$.,!?")
                for w in text_query.lower().split()
                if len(w.strip("$.,!?")) >= 3
                and w.strip("$.,!?") not in stop
                and not w.strip("$.,!?").isdigit()
            ][:6]
            if keywords:
                clauses = []
                for kw in keywords:
                    term = f"%{kw}%"
                    clauses.append(
                        f"(name ILIKE ${idx} OR description ILIKE ${idx} "
                        f"OR color ILIKE ${idx} OR subcategory ILIKE ${idx} "
                        f"OR brand ILIKE ${idx} OR category ILIKE ${idx})"
                    )
                    params.append(term)
                    idx += 1
                conditions.append(f"({' OR '.join(clauses)})")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = (  # nosec B608
            "SELECT * FROM vto_catalog "
            f"{where} "
            f"ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        )

        params.extend([query.limit, query.offset])
        rows = await self._db.fetch(sql, *params)
        return [_row_to_item(r) for r in rows]

    async def count_garments(self) -> int:
        """Return total row count in vto_catalog.

        Returns:
            Integer count.

        """
        return await self._db.fetchval("SELECT COUNT(*) FROM vto_catalog")


def _row_to_item(row: dict) -> GarmentItem:
    """Convert a DB row dict to GarmentItem."""
    item_id = str(row["item_id"])
    return GarmentItem(
        item_id=item_id,
        name=row["name"],
        category=row["category"],
        subcategory=row.get("subcategory"),
        overlay_category=row["overlay_category"],
        gender=row.get("gender", "unisex"),
        brand=row.get("brand"),
        color=row.get("color"),
        size_range=list(row["size_range"]) if row.get("size_range") else None,
        price=float(row["price"]) if row.get("price") is not None else None,
        description=row.get("description"),
        image_path=row["image_path"],
        embedding_id=row.get("embedding_id"),
        logistics_ref=row.get("logistics_ref"),
        branch_inventory_ref=row.get("branch_inventory_ref"),
        metadata=(
            json.loads(row["metadata"])
            if isinstance(row.get("metadata"), str)
            else row.get("metadata")
        ),
    )
