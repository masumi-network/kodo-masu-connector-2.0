-- Create table to track cron job executions
CREATE TABLE IF NOT EXISTS cron_executions (
    id SERIAL PRIMARY KEY,
    service_name VARCHAR(100) NOT NULL,
    execution_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) DEFAULT 'started', -- started, completed, failed
    items_processed INTEGER DEFAULT 0,
    error_message TEXT,
    duration_ms INTEGER
);

-- Create index for efficient queries
CREATE INDEX idx_cron_executions_service_time ON cron_executions (service_name, execution_time DESC);

-- Add some initial entries for existing services
INSERT INTO cron_executions (service_name, status, items_processed) VALUES
    ('authenticator', 'completed', 0),
    ('flow-sync', 'completed', 0),
    ('payment-checker', 'completed', 0),
    ('kodosumi-starter', 'completed', 0),
    ('kodosumi-status', 'completed', 0);