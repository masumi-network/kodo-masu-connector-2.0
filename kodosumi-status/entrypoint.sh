#!/bin/bash

# Export environment variables so cron can access them
printenv | grep -E '^(POSTGRES_|KODOSUMI_|PAYMENT_)' | sed 's/^\(.*\)$/export \1/g' > /etc/environment

# Start cron in foreground
echo "Starting Kodosumi Status Checker cron service..."
echo "Status checks will run every 2 minutes"

# Run initial check immediately
echo "Running initial status check..."
python /app/run_status_checker.py

# Start cron and tail the log
cron && tail -f /var/log/cron.log