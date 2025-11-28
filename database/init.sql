-- Ensure pgcrypto is available for UUID generation
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Create the API keys table
CREATE TABLE IF NOT EXISTS api_keys (
    id SERIAL PRIMARY KEY,
    api_key VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create a trigger to update the updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_api_keys_updated_at BEFORE UPDATE
    ON api_keys FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Specialized trigger for jobs to maintain status identifiers
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

-- Create the flows table
CREATE TABLE IF NOT EXISTS flows (
    id SERIAL PRIMARY KEY,
    uid VARCHAR(255) UNIQUE NOT NULL,
    author VARCHAR(255),
    deprecated BOOLEAN DEFAULT FALSE,
    description TEXT,
    method VARCHAR(10),
    organization VARCHAR(255),
    source VARCHAR(500),
    summary VARCHAR(255),
    tags TEXT[], -- Array of tags
    url VARCHAR(500),
    url_identifier VARCHAR(255), -- URL-friendly identifier like "YouTubeChannelAnalysis"
    input_schema JSONB, -- Store the original Kodosumi input schema as JSON
    mip003_schema JSONB, -- Store the MIP003 compliant schema as JSON
    agent_identifier VARCHAR(255), -- Legacy default agent identifier
    agent_identifier_default VARCHAR(255), -- Primary agent identifier
    premium_agent_identifier VARCHAR(255), -- Optional premium pricing identifier
    free_agent_identifier VARCHAR(255), -- Optional free-mode identifier
    free_mode_enabled BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create trigger for flows table updated_at
CREATE TRIGGER update_flows_updated_at BEFORE UPDATE
    ON flows FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_flows_uid ON flows(uid);
CREATE INDEX IF NOT EXISTS idx_flows_author ON flows(author);
CREATE INDEX IF NOT EXISTS idx_flows_updated_at ON flows(updated_at);
CREATE INDEX IF NOT EXISTS idx_flows_agent_identifier ON flows(agent_identifier);

-- Ensure flow names remain unique for upsert logic
ALTER TABLE flows
    ADD CONSTRAINT flows_summary_unique UNIQUE (summary);

-- Create the jobs table for tracking MIP003 job execution
CREATE TABLE IF NOT EXISTS jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_uid VARCHAR(255) NOT NULL,
    input_data JSONB NOT NULL,
    payment_data JSONB NOT NULL,
    identifier_from_purchaser VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    current_status_id UUID DEFAULT gen_random_uuid(),
    awaiting_input_status_id UUID,
    message TEXT,
    result JSONB,
    reasoning TEXT,
    input_hash VARCHAR(255),
    blockchain_identifier VARCHAR(255),
    agent_identifier_used VARCHAR(255),
    payment_required BOOLEAN DEFAULT TRUE,
    kodosumi_start_attempts INTEGER DEFAULT 0,
    waiting_for_start_in_kodosumi BOOLEAN DEFAULT FALSE,
    timeout_attempts INTEGER DEFAULT 0,
    not_found_attempts INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (flow_uid) REFERENCES flows(uid)
);

-- Create trigger for jobs table updated_at
CREATE TRIGGER update_jobs_metadata BEFORE UPDATE
    ON jobs FOR EACH ROW EXECUTE FUNCTION update_job_metadata();

-- Table to store HITL (lock) requests for jobs awaiting human input
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

CREATE TRIGGER update_job_input_requests_updated_at BEFORE UPDATE
    ON job_input_requests FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE INDEX IF NOT EXISTS idx_job_input_requests_job_pending
    ON job_input_requests (job_id) WHERE status = 'pending';

CREATE UNIQUE INDEX IF NOT EXISTS uniq_job_lock_pending
    ON job_input_requests (job_id, lock_identifier)
    WHERE status IN ('pending', 'awaiting');

-- Create indexes for jobs table
CREATE INDEX IF NOT EXISTS idx_jobs_flow_uid ON jobs(flow_uid);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_identifier_from_purchaser ON jobs(identifier_from_purchaser);
CREATE INDEX IF NOT EXISTS idx_jobs_blockchain_identifier ON jobs(blockchain_identifier);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_waiting_for_start ON jobs(waiting_for_start_in_kodosumi) WHERE waiting_for_start_in_kodosumi = true;
CREATE INDEX IF NOT EXISTS idx_jobs_kodosumi_attempts ON jobs(kodosumi_start_attempts);

-- Track cron job executions for dashboard visibility
CREATE TABLE IF NOT EXISTS cron_executions (
    id SERIAL PRIMARY KEY,
    service_name VARCHAR(100) NOT NULL,
    execution_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) DEFAULT 'started',
    items_processed INTEGER DEFAULT 0,
    error_message TEXT,
    duration_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_cron_executions_service_time ON cron_executions (service_name, execution_time DESC);

INSERT INTO cron_executions (service_name, status, items_processed)
SELECT label, 'completed', 0
FROM (VALUES
    ('authenticator'),
    ('flow-sync'),
    ('payment-checker'),
    ('kodosumi-starter'),
    ('kodosumi-status')
) AS services(label)
ON CONFLICT DO NOTHING;
