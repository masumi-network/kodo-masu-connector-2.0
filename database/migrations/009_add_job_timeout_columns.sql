-- Migration 009: add timeout and not-found tracking columns to jobs

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS timeout_attempts INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS not_found_attempts INTEGER DEFAULT 0;

-- Backfill historical rows to avoid NULL values
UPDATE jobs
SET timeout_attempts = COALESCE(timeout_attempts, 0),
    not_found_attempts = COALESCE(not_found_attempts, 0);
