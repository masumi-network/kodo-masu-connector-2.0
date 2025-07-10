#!/bin/bash

echo "Starting Kodosumi Service..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Error: .env file not found!"
    echo "Please create a .env file with the required configuration."
    exit 1
fi

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Stop any existing containers (preserving volumes/data)
echo "Stopping existing containers..."
docker compose down

echo "Building and starting services with Docker Compose..."

# Build and start the services
docker compose up --build -d

# Wait for services to be ready
echo "Waiting for services to start..."
sleep 15

# Check if services are running
echo "Checking service status..."
docker compose ps

# Wait for database to be ready and services to actually start
echo "Waiting for database and services to be fully ready..."
sleep 20

# Run the test scripts only if services are running
echo "Running authentication test script..."
if docker compose ps authenticator | grep -q "Up"; then
    docker compose exec -T authenticator python /app/test_authentication.py
else
    echo "Authenticator service is not running - skipping test"
fi

echo "Running flow sync test script..."
if docker compose ps flow-sync | grep -q "Up"; then
    docker compose exec -T flow-sync python /app/test_flow_sync.py
else
    echo "Flow sync service is not running - skipping test"
fi

echo "Startup complete!"
echo ""
echo "Services available:"
echo "  - API Server: http://localhost:8000"
echo "  - API Documentation: http://localhost:8000/docs"
echo "  - Admin Dashboard: http://localhost:5000"
echo "  - pgAdmin Database: http://localhost:8080"
echo ""
echo "To view logs:"
echo "  - All services: docker compose logs -f"
echo "  - Database only: docker compose logs -f postgres"
echo "  - API server only: docker compose logs -f api-server"
echo "  - Authenticator only: docker compose logs -f authenticator"
echo "  - Flow sync only: docker compose logs -f flow-sync"
echo "  - Payment checker only: docker compose logs -f payment-checker"
echo "  - Kodosumi starter only: docker compose logs -f kodosumi-starter"
echo "  - Kodosumi status only: docker compose logs -f kodosumi-status"
echo "  - Admin dashboard only: docker compose logs -f admin-dashboard"
echo "  - pgAdmin only: docker compose logs -f pgadmin"
echo ""
echo "To stop services: docker compose down"
echo "To stop and remove volumes: docker compose down -v"