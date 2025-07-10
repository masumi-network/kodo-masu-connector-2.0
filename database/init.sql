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

-- Create the jobs table for tracking MIP003 job execution
CREATE TABLE IF NOT EXISTS jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_uid VARCHAR(255) NOT NULL,
    input_data JSONB NOT NULL,
    payment_data JSONB NOT NULL,
    identifier_from_purchaser VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    message TEXT,
    result JSONB,
    reasoning TEXT,
    input_hash VARCHAR(255),
    blockchain_identifier VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (flow_uid) REFERENCES flows(uid)
);

-- Create trigger for jobs table updated_at
CREATE TRIGGER update_jobs_updated_at BEFORE UPDATE
    ON jobs FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Create indexes for jobs table
CREATE INDEX IF NOT EXISTS idx_jobs_flow_uid ON jobs(flow_uid);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_identifier_from_purchaser ON jobs(identifier_from_purchaser);
CREATE INDEX IF NOT EXISTS idx_jobs_blockchain_identifier ON jobs(blockchain_identifier);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);