<!--
Copyright Advanced Micro Devices, Inc.

SPDX-License-Identifier: MIT
-->

# Skill: catalog-mapping

## Purpose

Map natural-language shopper preferences to structured catalog query
parameters for use with `search_milvus_rag_tool` and `query_pgvector_tool`.

## When to Apply

Apply before every catalog search to ensure shopper intent is correctly
translated into catalog-compatible filters.

## Mapping Rules

### Occasion → Category/Subcategory

| Shopper says | category | subcategory hints |
|---|---|---|
| casual, everyday, weekend | tops/bottoms | t-shirts, jeans, shorts |
| formal, office, work, interview | tops/bottoms/one-pieces | shirts, trousers, dresses, suits |
| smart-casual | tops/bottoms | shirts, chinos, blouses |
| party, evening | one-pieces | dresses, jumpsuits |
| gym, workout, sports | bottoms | leggings, track-pants, joggers |
| traditional, ethnic | tops/one-pieces | kurtas, anarkalis, sherwanis |
| outdoor, layer | outerwear | jackets, windbreakers, blazers |

### Garment Type → overlay_category

| Described as | overlay_category |
|---|---|
| top, shirt, blouse, kurta, hoodie | tops |
| pants, jeans, skirt, shorts, leggings | bottoms |
| dress, jumpsuit, suit, saree set, romper | one-pieces |
| jacket, coat, blazer, shrug | tops (outerwear → tops) |

### Size/Fit → metadata filter (if available)

Extract size mentions (XS, S, M, L, XL, XXL) and pass as metadata filter.
If no size mentioned, do not filter by size.

## Output Format

```json
{
  "search_query": "<refined natural language query for Milvus>",
  "category_filter": "<category or null>",
  "overlay_category_filter": "<tops|bottoms|one-pieces or null>",
  "color_hint": "<color or null>",
  "occasion": "<casual|formal|smart-casual|...>"
}
```
