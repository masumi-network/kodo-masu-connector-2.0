FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including cron
RUN apt-get update && apt-get install -y \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY flow-sync/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the flow sync script and dependencies
COPY flow-sync/flow_sync.py .
COPY flow-sync/mip003_converter.py .
COPY flow-sync/cron_logger.py .

# Make the script executable
RUN chmod +x flow_sync.py

# Copy crontab file
COPY flow-sync/crontab /etc/cron.d/flow-sync

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/flow-sync

# Apply cron job
RUN crontab /etc/cron.d/flow-sync

# Create the log file to be able to run tail
RUN touch /var/log/flow-sync.log

# Copy entrypoint script
COPY flow-sync/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]