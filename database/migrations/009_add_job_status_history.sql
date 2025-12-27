BEGIN;

CREATE TABLE IF NOT EXISTS job_status_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL,
    status_id UUID NOT NULL,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_job_status_events_job_time
    ON job_status_events (job_id, created_at);

CREATE OR REPLACE FUNCTION log_job_status_event()
RETURNS TRIGGER AS $$
DECLARE
    effective_status_id UUID;
BEGIN
    effective_status_id := COALESCE(NEW.current_status_id, gen_random_uuid());

    INSERT INTO job_status_events (job_id, status, status_id, message, created_at)
    VALUES (
        NEW.job_id,
        NEW.status,
        effective_status_id,
        NEW.message,
        COALESCE(NEW.updated_at, CURRENT_TIMESTAMP)
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS job_status_events_after_insert ON jobs;
DROP TRIGGER IF EXISTS job_status_events_after_update ON jobs;

CREATE TRIGGER job_status_events_after_insert
AFTER INSERT ON jobs
FOR EACH ROW EXECUTE FUNCTION log_job_status_event();

CREATE TRIGGER job_status_events_after_update
AFTER UPDATE ON jobs
FOR EACH ROW
WHEN (NEW.current_status_id IS DISTINCT FROM OLD.current_status_id)
EXECUTE FUNCTION log_job_status_event();

INSERT INTO job_status_events (job_id, status, status_id, message, created_at)
SELECT job_id, status, current_status_id, message, COALESCE(updated_at, created_at)
FROM jobs
ON CONFLICT DO NOTHING;

COMMIT;
