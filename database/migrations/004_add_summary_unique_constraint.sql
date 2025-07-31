-- Migration: Use flow summary as unique key instead of uid
-- This prevents duplicate flows with the same name
-- Date: 2025-07-31

-- First, update any jobs that reference duplicate flows to point to the most recent version
WITH duplicate_mappings AS (
    SELECT 
        f1.uid as old_uid,
        f2.uid as new_uid,
        f1.summary
    FROM flows f1
    JOIN (
        SELECT DISTINCT ON (summary) summary, uid, updated_at
        FROM flows
        ORDER BY summary, updated_at DESC
    ) f2 ON f1.summary = f2.summary
    WHERE f1.uid != f2.uid
)
UPDATE jobs j
SET flow_uid = dm.new_uid
FROM duplicate_mappings dm
WHERE j.flow_uid = dm.old_uid;

-- Delete duplicate flows, keeping only the most recently updated one for each summary
DELETE FROM flows
WHERE id NOT IN (
    SELECT DISTINCT ON (summary) id
    FROM flows
    ORDER BY summary, updated_at DESC
);

-- Add unique constraint on summary to prevent future duplicates
ALTER TABLE flows 
ADD CONSTRAINT flows_summary_unique UNIQUE (summary);

-- Note: We keep the uid column and its uniqueness for backward compatibility
-- The flow sync process will use summary for conflict resolution