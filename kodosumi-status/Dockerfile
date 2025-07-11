FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install cron
RUN apt-get update && apt-get install -y \
    gcc \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY kodosumi-status/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY kodosumi-status/ .

# Copy shared cron logger module
COPY shared/cron_logger.py .

# Copy api-server files for payment_service module
COPY api-server/ /app/api-server/

# Make scripts executable
RUN chmod +x run_status_checker.py

# Copy crontab file
COPY kodosumi-status/crontab /etc/cron.d/kodosumi-status

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/kodosumi-status

# Apply cron job
RUN crontab /etc/cron.d/kodosumi-status

# Create the log file to be able to run tail
RUN touch /var/log/cron.log

# Copy entrypoint script
COPY kodosumi-status/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]