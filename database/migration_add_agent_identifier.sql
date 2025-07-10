-- Add agent_identifier column to flows table
ALTER TABLE flows ADD COLUMN IF NOT EXISTS agent_identifier VARCHAR(255);

-- Create index for better performance
CREATE INDEX IF NOT EXISTS idx_flows_agent_identifier ON flows(agent_identifier);