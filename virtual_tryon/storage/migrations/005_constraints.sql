-- Copyright Advanced Micro Devices, Inc.
--
-- SPDX-License-Identifier: MIT
--
-- Data-integrity constraints for existing tables.
-- Safe to run on a live database: back-fills NULLs before altering, and
-- existing feedback rows are truncated to 5000 chars before the type change.
-- All ADD CONSTRAINT statements are guarded with IF NOT EXISTS.

-- ── vto_catalog.price ────────────────────────────────────────────────────────
-- Back-fill any NULL prices so the default can take effect for future inserts.
UPDATE vto_catalog SET price = 0.00 WHERE price IS NULL;

-- Default protects against future INSERT statements that omit price.
ALTER TABLE vto_catalog
    ALTER COLUMN price SET DEFAULT 0.00;

-- Reject negative prices at the database layer.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_catalog_price_nonneg'
        AND conrelid = 'vto_catalog'::regclass
    ) THEN
        ALTER TABLE vto_catalog
            ADD CONSTRAINT chk_catalog_price_nonneg CHECK (price >= 0);
    END IF;
END $$;

-- ── vto_tryon_history.rating ─────────────────────────────────────────────────
-- NULL is allowed (rating is optional); non-NULL values must be 0–5.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_tryon_rating_range'
        AND conrelid = 'vto_tryon_history'::regclass
    ) THEN
        ALTER TABLE vto_tryon_history
            ADD CONSTRAINT chk_tryon_rating_range CHECK (rating BETWEEN 0 AND 5);
    END IF;
END $$;

-- ── vto_customer_feedback.feedback ───────────────────────────────────────────
-- Truncate any over-length rows before narrowing the column type.
UPDATE vto_customer_feedback
    SET feedback = left(feedback, 5000)
    WHERE length(feedback) > 5000;

ALTER TABLE vto_customer_feedback
    ALTER COLUMN feedback TYPE VARCHAR(5000);
