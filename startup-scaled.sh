#!/bin/bash

echo "Starting Kodo-Masu Connector 2.0 with horizontal scaling..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Error: .env file not found!"
    echo "Please copy .env.example to .env and configure it."
    exit 1
fi

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Stop any existing containers
echo "Stopping existing containers..."
docker compose -f docker compose.yml -f docker compose.scale.yml down

# Build images
echo "Building Docker images..."
docker compose -f docker compose.yml -f docker compose.scale.yml build

# Start services with scaling
echo "Starting services with horizontal scaling..."
docker compose -f docker compose.yml -f docker compose.scale.yml up -d --scale api-server=3

# Wait for services to be ready
echo "Waiting for services to be ready..."
sleep 10

# Show running containers
echo "Running containers:"
docker compose -f docker compose.yml -f docker compose.scale.yml ps

echo ""
echo "Kodo-Masu Connector 2.0 is now running with horizontal scaling!"
echo "API Server: http://localhost:8000 (load balanced across 3 instances)"
echo "Admin Dashboard: http://localhost:5000"
echo "PgAdmin: http://localhost:8080"
echo ""
echo "To view logs: docker compose -f docker compose.yml -f docker compose.scale.yml logs -f"
echo "To stop: docker compose -f docker compose.yml -f docker compose.scale.yml down"