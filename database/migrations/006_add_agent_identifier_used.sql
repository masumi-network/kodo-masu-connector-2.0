-- Migration: Ensure jobs table has agent_identifier_used column
-- Date: 2025-09-23

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS agent_identifier_used VARCHAR(255);
