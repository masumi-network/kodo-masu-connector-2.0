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

# Create cron job to run every 30 minutes
RUN echo "*/30 * * * * cd /app && python3 flow_sync.py >> /var/log/flow-sync.log 2>&1" > /etc/cron.d/kodosumi-flow-sync

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/kodosumi-flow-sync

# Apply cron job
RUN crontab /etc/cron.d/kodosumi-flow-sync

# Create the log file to be able to run tail
RUN touch /var/log/flow-sync.log

# Run initial flow sync then start cron
CMD python3 flow_sync.py && cron && tail -f /var/log/flow-sync.log