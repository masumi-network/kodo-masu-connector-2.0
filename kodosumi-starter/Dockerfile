FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies including cron
RUN apt-get update && apt-get install -y \
    gcc \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY kodosumi-starter/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY kodosumi-starter/ .

# Make the run script executable
RUN chmod +x run_starter.py

# Copy crontab file
COPY kodosumi-starter/crontab /etc/cron.d/kodosumi-starter

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/kodosumi-starter

# Apply cron job
RUN crontab /etc/cron.d/kodosumi-starter

# Create the log file to be able to run tail
RUN touch /var/log/cron.log

# Copy and set up entrypoint script
COPY kodosumi-starter/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run cron and tail logs
CMD ["/entrypoint.sh"]