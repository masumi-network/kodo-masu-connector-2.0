-- Add kodosumi_start_attempts column to jobs table
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS kodosumi_start_attempts INTEGER DEFAULT 0;

-- Add waiting_for_start_in_kodosumi column to jobs table
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS waiting_for_start_in_kodosumi BOOLEAN DEFAULT false;

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_jobs_waiting_for_start ON jobs(waiting_for_start_in_kodosumi) WHERE waiting_for_start_in_kodosumi = true;
CREATE INDEX IF NOT EXISTS idx_jobs_kodosumi_attempts ON jobs(kodosumi_start_attempts);