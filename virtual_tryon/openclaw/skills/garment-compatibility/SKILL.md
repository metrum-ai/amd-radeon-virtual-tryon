<!--
Copyright Advanced Micro Devices, Inc.

SPDX-License-Identifier: MIT
-->

# Skill: garment-compatibility

## Purpose

Validate that a combination of garments can be worn together and
flag incompatible combinations before publishing recommendations.

## When to Apply

Apply before finalising a recommendation set to remove incompatible pairs.

## Compatibility Matrix

| Overlay category | Compatible with | Incompatible with |
|---|---|---|
| tops | bottoms, outerwear | one-pieces |
| bottoms | tops, outerwear | one-pieces |
| one-pieces | outerwear | tops, bottoms |
| outerwear | tops, bottoms, one-pieces | — |

## Validation Steps

1. For each pair (garment_A, garment_B) in the recommended set:
   - Get `overlay_category` for both garments
   - Check against the compatibility matrix
2. Remove any garment whose `overlay_category` is incompatible with
   the currently selected base garment
3. Flag removed items with `removed_reason: "incompatible_overlay_category"`

## Special Cases

- If a shopper is wearing a `one-piece`, only suggest `outerwear` layers
- Multiple `tops` or multiple `bottoms` in one recommendation set
  is allowed as alternatives (e.g. "try this top instead")
- Never remove items solely based on price or availability; that is
  handled by the `logistics-enrichment` skill

## Output Format

```json
{
  "compatible": ["<item_id>", ...],
  "removed": [
    { "item_id": "<uuid>", "removed_reason": "incompatible_overlay_category" }
  ]
}
```
