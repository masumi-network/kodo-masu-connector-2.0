-- Migration 010: ensure jobs stay linked to flows when UIDs change

ALTER TABLE jobs
    DROP CONSTRAINT IF EXISTS jobs_flow_uid_fkey;

ALTER TABLE jobs
    ADD CONSTRAINT jobs_flow_uid_fkey
        FOREIGN KEY (flow_uid) REFERENCES flows(uid)
        ON UPDATE CASCADE
        ON DELETE RESTRICT;
