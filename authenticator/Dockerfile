FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including cron
RUN apt-get update && apt-get install -y \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY authenticator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the authenticator script and cron logger
COPY authenticator/authenticator.py .
COPY authenticator/cron_logger.py .

# Make the script executable
RUN chmod +x authenticator.py

# Copy crontab file
COPY authenticator/crontab /etc/cron.d/authenticator

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/authenticator

# Apply cron job
RUN crontab /etc/cron.d/authenticator

# Create the log file to be able to run tail
RUN touch /var/log/cron.log

# Copy entrypoint script
COPY authenticator/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]