#!/bin/bash

# Export environment variables so cron can access them with proper escaping
printenv | grep -E '^(POSTGRES_|KODOSUMI_|PAYMENT_)' | sed 's/=\(.*\)/="\1"/' | sed 's/^/export /' > /etc/environment

# Start cron in foreground
echo "Starting Authenticator cron service..."
echo "API key sync will run every 10 hours"

# Run initial authentication immediately
echo "Running initial API key sync..."
python /app/authenticator.py

# Start cron and tail the log
cron && tail -f /var/log/cron.log