-- Copyright Advanced Micro Devices, Inc.
--
-- SPDX-License-Identifier: MIT
--
-- Add gender column to vto_catalog (women | men | unisex)
ALTER TABLE vto_catalog
    ADD COLUMN IF NOT EXISTS gender TEXT NOT NULL DEFAULT 'unisex'
        CHECK (gender IN ('women', 'men', 'unisex'));

CREATE INDEX IF NOT EXISTS idx_vto_catalog_gender
    ON vto_catalog (gender);
