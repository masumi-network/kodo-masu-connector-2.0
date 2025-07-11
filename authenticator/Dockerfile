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

# Create cron job to run every 10 hours
RUN echo "0 */10 * * * cd /app && python3 authenticator.py >> /var/log/cron.log 2>&1" > /etc/cron.d/kodosumi-auth

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/kodosumi-auth

# Apply cron job
RUN crontab /etc/cron.d/kodosumi-auth

# Create the log file to be able to run tail
RUN touch /var/log/cron.log

# Run initial authentication then start cron
CMD python3 authenticator.py && cron && tail -f /var/log/cron.log