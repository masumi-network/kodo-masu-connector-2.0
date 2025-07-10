#!/bin/bash

# Export all environment variables for cron
printenv | grep -v "no_proxy" >> /etc/environment

# Start cron in the background
cron

echo "Kodosumi Job Starter Service with cron initialized"
echo "Jobs will run every minute. Following log output..."

# Follow the log file
tail -f /var/log/cron.log