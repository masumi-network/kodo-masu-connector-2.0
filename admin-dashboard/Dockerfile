FROM python:3.11-slim

WORKDIR /app

# Install Docker CLI
RUN apt-get update && apt-get install -y \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY admin-dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY admin-dashboard/ .

# Expose port
EXPOSE 5000

# Run the application
CMD ["python", "app.py"]