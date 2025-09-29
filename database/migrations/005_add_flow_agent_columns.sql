-- Migration: Add agent identifier columns to flows
-- Ensures existing databases match updated schema expected by admin dashboard
-- Date: 2025-09-23

ALTER TABLE flows
    ADD COLUMN IF NOT EXISTS agent_identifier_default VARCHAR(255),
    ADD COLUMN IF NOT EXISTS premium_agent_identifier VARCHAR(255),
    ADD COLUMN IF NOT EXISTS free_agent_identifier VARCHAR(255),
    ADD COLUMN IF NOT EXISTS free_mode_enabled BOOLEAN DEFAULT FALSE;

-- Backfill new columns using existing data when available
UPDATE flows
SET agent_identifier_default = COALESCE(agent_identifier_default, agent_identifier),
    free_mode_enabled = COALESCE(free_mode_enabled, FALSE)
WHERE agent_identifier IS NOT NULL;

-- Ensure future updates keep updated_at current
ALTER TABLE flows
    ALTER COLUMN free_mode_enabled SET DEFAULT FALSE;
