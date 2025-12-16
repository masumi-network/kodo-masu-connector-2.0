FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY payment-checker/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY payment-checker/ .

# Copy shared utilities
COPY shared ./shared

# Make the scripts executable
RUN chmod +x payment_checker.py run_checker.py

# Default command - run the service wrapper
CMD ["python", "run_checker.py"]
