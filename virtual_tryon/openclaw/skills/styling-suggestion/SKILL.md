<!--
Copyright Advanced Micro Devices, Inc.

SPDX-License-Identifier: MIT
-->

# Skill: styling-suggestion

## Purpose

Generate catalog-bound outfit suggestions and compatible item pairings
based on a selected garment and shopper preferences.

## When to Apply

Apply after a shopper selects a garment to suggest complementary pieces
that complete the outfit. All suggestions must reference real catalog IDs.

## Steps

1. Identify the selected garment's `overlay_category`
2. Look up compatible overlay categories from the compatibility matrix:
   - `tops` → pair with `bottoms`, optionally `outerwear`
   - `bottoms` → pair with `tops`, optionally `outerwear`
   - `one-pieces` → pair with `outerwear` only
3. Search for compatible garments in the paired category:
   - Use `search_milvus_rag_tool` with the shopper's colour/occasion context
   - Filter by the compatible `overlay_category`
4. Apply color harmony to rank candidates:
   - navy: prefers white, beige, gold
   - black: prefers white, red, gold
   - white: prefers navy, black, olive
   - beige: prefers brown, white, olive
5. Select up to 5 complementary items
6. Generate brief reasoning per item (1 sentence, 80 chars max)

## Output Format

```json
{
  "base_garment_id": "<uuid>",
  "complementary": [
    {
      "item_id": "<uuid>",
      "overlay_category": "bottoms",
      "recommendation_type": "complement",
      "reasoning": "Navy chinos balance the white linen shirt well.",
      "score": 0.88
    }
  ]
}
```

## Constraints

- Never recommend the same item as the base garment
- Never recommend items not returned by catalog tools
- Keep reasoning to 1 sentence per item
