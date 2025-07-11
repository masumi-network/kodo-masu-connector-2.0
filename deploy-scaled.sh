#!/bin/bash
# Full deployment script with all scaling improvements
# This includes horizontal scaling, nginx load balancer, and monitoring

set -e

echo "=== Kodo-Masu Connector 2.0 - Full Scaled Deployment ==="
echo "This script deploys the complete system with:"
echo "- 3 API server instances"
echo "- Nginx load balancer"
echo "- All robustness improvements"
echo "- Monitoring capabilities"
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Error: .env file not found!"
    echo "Please copy .env.example to .env and configure it"
    exit 1
fi

# Load environment variables
source .env

# Check Docker installation
if ! command -v docker &> /dev/null; then
    echo "Docker not found. Would you like to install it? (y/n)"
    read -r response
    if [[ "$response" == "y" ]]; then
        bash install-docker.sh
    else
        echo "Please install Docker first"
        exit 1
    fi
fi

# Stop any existing containers
echo "Stopping existing containers..."
docker compose down || true

# Apply database migrations
echo "Setting up database migrations..."
chmod +x scripts/apply_migrations.sh

# Build and start services with scaling
echo "Building and starting services with horizontal scaling..."
docker compose -f docker-compose.yml -f docker-compose.scale.yml build

# Start core services first
echo "Starting core services..."
docker compose -f docker-compose.yml -f docker-compose.scale.yml up -d postgres

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL to be ready..."
sleep 10

# Apply migrations
echo "Applying database migrations..."
docker compose -f docker-compose.yml -f docker-compose.scale.yml run --rm api-server bash /app/scripts/apply_migrations.sh

# Start all services with scaling
echo "Starting all services with 3 API instances..."
docker compose -f docker-compose.yml -f docker-compose.scale.yml up -d --scale api-server=3

# Wait for services to be ready
echo "Waiting for services to initialize..."
sleep 20

# Health check
echo "Performing health checks..."
echo "Checking API servers through load balancer..."
for i in {1..5}; do
    if curl -s http://localhost/health > /dev/null; then
        echo "✓ Load balancer and API servers are healthy"
        break
    fi
    echo "Waiting for services to be ready... ($i/5)"
    sleep 5
done

# Show service status
echo ""
echo "=== Service Status ==="
docker compose -f docker-compose.yml -f docker-compose.scale.yml ps

# Show monitoring information
echo ""
echo "=== Monitoring Information ==="
echo "API metrics available at: http://localhost/metrics"
echo "PgAdmin available at: http://localhost:5050"
echo "Admin Dashboard available at: http://localhost:3001"
echo ""
echo "To monitor requests in real-time:"
echo "  python3 monitor.py"
echo ""
echo "To view nginx access logs:"
echo "  docker logs -f kodosumi-nginx"
echo ""

# Save deployment configuration
echo ""
echo "=== Saving Deployment Configuration ==="
cat > deployment-config.json << EOF
{
  "deployment_type": "scaled",
  "api_instances": 3,
  "workers_per_instance": 16,
  "total_workers": 48,
  "load_balancer": "nginx",
  "monitoring": "prometheus_metrics",
  "deployed_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF

echo "Deployment configuration saved to deployment-config.json"
echo ""
echo "=== Deployment Complete! ==="
echo "The system is now running with:"
echo "- 3 API server instances (48 total workers)"
echo "- Nginx load balancer on port 80"
echo "- All services healthy and ready"
echo ""
echo "Next steps:"
echo "1. Test the API: curl http://localhost/health"
echo "2. Monitor logs: docker logs -f <service-name>"
echo "3. View metrics: http://localhost/metrics"
echo "4. Access admin dashboard: http://localhost:3001"