-- Migration 008: Add Human-in-the-loop (HITL) support tables and columns

-- Ensure pgcrypto is available for gen_random_uuid
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Add status tracking columns to jobs
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS current_status_id UUID DEFAULT gen_random_uuid(),
    ADD COLUMN IF NOT EXISTS awaiting_input_status_id UUID;

-- Backfill current_status_id for existing rows
UPDATE jobs
SET current_status_id = COALESCE(current_status_id, gen_random_uuid());

-- Replace the generic jobs trigger with one that also maintains status identifiers
DROP TRIGGER IF EXISTS update_jobs_updated_at ON jobs;
DROP TRIGGER IF EXISTS update_jobs_metadata ON jobs;

CREATE OR REPLACE FUNCTION update_job_metadata()
RETURNS TRIGGER AS $$
BEGIN
    IF (NEW.current_status_id IS NULL OR NEW.current_status_id = OLD.current_status_id) THEN
        IF NEW.status IS DISTINCT FROM OLD.status
            OR NEW.message IS DISTINCT FROM OLD.message
            OR NEW.reasoning IS DISTINCT FROM OLD.reasoning
            OR NEW.awaiting_input_status_id IS DISTINCT FROM OLD.awaiting_input_status_id THEN
            NEW.current_status_id = gen_random_uuid();
        END IF;
    END IF;
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_jobs_metadata
    BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION update_job_metadata();

-- Table to track pending human input requests (locks)
CREATE TABLE IF NOT EXISTS job_input_requests (
    status_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    kodosumi_fid VARCHAR(255) NOT NULL,
    lock_identifier VARCHAR(255) NOT NULL,
    schema_raw JSONB,
    schema_mip003 JSONB,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    response_data JSONB,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Trigger to keep updated_at fresh
DROP TRIGGER IF EXISTS update_job_input_requests_updated_at ON job_input_requests;
CREATE TRIGGER update_job_input_requests_updated_at
    BEFORE UPDATE ON job_input_requests
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE INDEX IF NOT EXISTS idx_job_input_requests_job_pending
    ON job_input_requests (job_id)
    WHERE status = 'pending';

CREATE UNIQUE INDEX IF NOT EXISTS uniq_job_lock_pending
    ON job_input_requests (job_id, lock_identifier)
    WHERE status IN ('pending', 'awaiting');
