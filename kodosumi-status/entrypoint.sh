#!/bin/bash

# Export environment variables so cron can access them with proper escaping
printenv | grep -E '^(POSTGRES_|KODOSUMI_|PAYMENT_)' | sed 's/=\(.*\)/="\1"/' | sed 's/^/export /' > /etc/environment

# Start cron in foreground
echo "Starting Kodosumi Status Checker cron service..."
echo "Status checks will run every 30 seconds"

# Run initial check immediately
echo "Running initial status check..."
python /app/run_status_checker_with_logging.py

# Start cron and tail the log
cron && tail -f /var/log/cron.log
