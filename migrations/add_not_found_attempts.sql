-- Add not_found_attempts column to track 404 retries
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS not_found_attempts INTEGER DEFAULT 0;

-- Create index for efficient queries
CREATE INDEX IF NOT EXISTS idx_jobs_not_found_attempts ON jobs(not_found_attempts) WHERE status = 'running';