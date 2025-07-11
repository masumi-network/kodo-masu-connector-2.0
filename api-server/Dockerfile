FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY api-server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY api-server/ .

# Expose port
EXPOSE 8000

# Run the application with more workers for better concurrency
# Increased to 16 workers for handling high load on availability endpoints
# Also added worker-connections for better handling of concurrent connections
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "16", "--loop", "uvloop", "--limit-concurrency", "1000"]