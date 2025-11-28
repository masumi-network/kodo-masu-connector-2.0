-- Migration: Ensure jobs table tracks whether payment is required
-- Keeps existing deployments in sync with updated init schema
-- Date: 2025-09-29

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS payment_required BOOLEAN DEFAULT TRUE;

-- Backfill existing rows so NULLs default to TRUE for historical jobs
UPDATE jobs
SET payment_required = COALESCE(payment_required, TRUE);
