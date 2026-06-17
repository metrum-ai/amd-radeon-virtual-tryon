# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Catalog gender filtering helpers."""

from __future__ import annotations


def catalog_gender(gender: str | None) -> str | None:
    """Return supported catalog gender filter."""
    return gender if gender in ("women", "men") else None


def gender_matches(
    item_gender: str | None, customer_gender: str | None
) -> bool:
    """Return whether a garment is allowed for the selected customer."""
    gender = catalog_gender(customer_gender)
    if gender is None:
        return True
    return item_gender in (gender, "unisex")
