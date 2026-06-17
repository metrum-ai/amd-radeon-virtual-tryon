<!--
Copyright Advanced Micro Devices, Inc.

SPDX-License-Identifier: MIT
-->

# Skill: logistics-enrichment

## Purpose

Enrich a list of catalog garment IDs with branch availability, stock status,
display price, and fulfillment options using the VTO MCP tools.

## When to Apply

Apply this skill when you have a list of catalog item_ids and need to add
price and availability data before returning them as recommendation cards.

## Steps

1. For each `item_id`:
   - Call `get_branch_availability_tool(item_id)` → availability status + branches
   - Call `get_price_quote_tool(item_id)` → price + currency

2. Merge results into `GarmentLogistics` per item:
   ```json
   {
     "item_id": "<uuid>",
     "display_price": <float or null>,
     "currency": "INR",
     "discount_label": null,
     "availability_status": "in_stock" | "low_stock" | "out_of_stock" | "unknown",
     "available_branches": [...],
     "preferred_branch_id": "<uuid or null>"
   }
   ```

3. Apply recommendation policy:
   - Keep `in_stock` and `low_stock` as primary recommendations
   - Move `out_of_stock` to secondary (badge as unavailable)
   - Keep `unknown` with async-retry flag

4. Publish `logistics.enriched` event after all items processed

## Error Handling

- If tool returns no rows: set `availability_status: unknown`; do NOT guess
- If tool times out: mark `stale: true`; include in results with unknown status
- Never block the recommendation card from rendering due to missing logistics
