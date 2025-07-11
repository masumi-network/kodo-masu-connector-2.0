#!/bin/bash

# Export environment variables so cron can access them with proper escaping
printenv | grep -E '^(POSTGRES_|KODOSUMI_|PAYMENT_)' | sed 's/=\(.*\)/="\1"/' | sed 's/^/export /' > /etc/environment

# Start cron in foreground
echo "Starting Flow Sync cron service..."
echo "Flow sync will run every 30 minutes"

# Run initial sync immediately
echo "Running initial flow sync..."
python /app/flow_sync.py

# Start cron and tail the log
cron && tail -f /var/log/flow-sync.log