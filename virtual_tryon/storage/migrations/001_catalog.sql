-- Copyright Advanced Micro Devices, Inc.
--
-- SPDX-License-Identifier: MIT
--
-- F4: Garment Catalog, Branches, and Branch Inventory tables

CREATE TABLE IF NOT EXISTS vto_catalog (
    item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT,
    overlay_category TEXT NOT NULL,
    gender TEXT NOT NULL DEFAULT 'unisex'
        CHECK (gender IN ('women', 'men', 'unisex')),
    brand TEXT,
    color TEXT,
    size_range TEXT[],
    price DECIMAL(10, 2),
    description TEXT,
    image_path TEXT NOT NULL,
    embedding_id TEXT,
    logistics_ref TEXT,
    branch_inventory_ref TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vto_catalog_category
    ON vto_catalog (category, subcategory);
CREATE INDEX IF NOT EXISTS idx_vto_catalog_overlay
    ON vto_catalog (overlay_category);

CREATE TABLE IF NOT EXISTS vto_branches (
    branch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    address TEXT,
    metadata JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vto_branch_inventory (
    item_id UUID REFERENCES vto_catalog(item_id) ON DELETE CASCADE,
    branch_id UUID REFERENCES vto_branches(branch_id) ON DELETE CASCADE,
    stock_on_hand INTEGER,
    available_to_promise INTEGER,
    display_price DECIMAL(10, 2),
    currency TEXT NOT NULL DEFAULT 'INR',
    availability_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (availability_status IN (
            'in_stock', 'low_stock', 'out_of_stock', 'unknown'
        )),
    pickup_eta_minutes INTEGER,
    fulfillment_options TEXT[],
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (item_id, branch_id)
);

CREATE INDEX IF NOT EXISTS idx_vto_branch_inventory_item
    ON vto_branch_inventory (item_id);
CREATE INDEX IF NOT EXISTS idx_vto_branch_inventory_branch
    ON vto_branch_inventory (branch_id);
