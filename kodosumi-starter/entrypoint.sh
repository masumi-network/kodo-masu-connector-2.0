#!/bin/bash

# Export all environment variables for cron with proper escaping
printenv | grep -v "no_proxy" | sed 's/=\(.*\)/="\1"/' | sed 's/^/export /' > /etc/environment

# Start cron in the background
cron

echo "Kodosumi Job Starter Service with cron initialized"
echo "Jobs will run every minute. Following log output..."

# Follow the log file
tail -f /var/log/cron.log