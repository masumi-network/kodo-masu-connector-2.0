FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
COPY admin-dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY admin-dashboard/ .

# Expose port
EXPOSE 5000

# Run the application
CMD ["python", "app.py"]