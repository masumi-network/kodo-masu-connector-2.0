# Deployment Guide

## Prerequisites

- Ubuntu 20.04+ or similar Linux distribution
- Root or sudo access
- Git installed
- At least 4GB RAM (8GB recommended for scaled deployment)
- At least 20GB free disk space

## Deployment Options

You can choose between two deployment configurations:

1. **Basic Deployment**: Single API instance with 16 workers (suitable for development/low traffic)
2. **Scaled Deployment**: 3 API instances with load balancer (recommended for production)

## Installation Steps

### 1. Clone the Repository

```bash
git clone https://github.com/masumi-network/kodo-masu-connector-2.0.git
cd kodo-masu-connector-2.0
```

### 2. Install Docker and Docker Compose

If Docker and Docker Compose are not installed, run the installation script:

```bash
./install-docker.sh
```

After installation, either:
- Log out and log back in, OR
- Run `newgrp docker` to apply group changes

### 3. Configure Environment Variables

Copy the example environment file and edit it with your settings:

```bash
cp .env.example .env
nano .env  # or use your preferred editor
```

Update the following variables:
- `KODOSUMI_SERVER_URL`: Your Kodosumi server URL
- `KODOSUMI_USERNAME`: Your Kodosumi username
- `KODOSUMI_PASSWORD`: Your Kodosumi password
- `PAYMENT_SERVICE_URL`: Payment service endpoint
- `PAYMENT_API_KEY`: Payment service API key
- `AGENT_IDENTIFIER`: Your agent identifier
- `POSTGRES_PASSWORD`: Choose a secure database password

### 4. Start the Services

#### Option A: Basic Deployment (Single Instance)

Run the standard startup script:

```bash
./startup.sh
```

This will:
- Build all Docker containers
- Start all services with a single API instance (16 workers)
- Initialize the database
- Run initial authentication and flow sync

#### Option B: Scaled Deployment (3 Instances + Load Balancer)

Run the scaled deployment script:

```bash
./deploy-scaled.sh
```

This will:
- Build all Docker containers
- Start 3 API server instances (48 total workers)
- Configure Nginx load balancer
- Initialize the database with optimized settings
- Run initial authentication and flow sync
- Enable advanced monitoring capabilities

### 5. Verify Installation

Check that all services are running:

```bash
docker compose ps
```

All services should show as "Up" or "healthy".

### 6. Access the Services

#### Basic Deployment Endpoints:
- **API Server**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs
- **Admin Dashboard**: http://localhost:3001
- **pgAdmin**: http://localhost:5050

#### Scaled Deployment Endpoints:
- **API Server (Load Balanced)**: http://localhost
- **API Documentation**: http://localhost/docs
- **Metrics Endpoint**: http://localhost/metrics
- **Admin Dashboard**: http://localhost:3001
- **pgAdmin**: http://localhost:5050

## What's Included in Each Deployment

### Basic Deployment Features:
- Single API server with 16 workers
- All core services (authenticator, flow-sync, payment-checker, kodosumi-starter/status)
- Rate limiting (60 requests/minute per endpoint)
- Circuit breakers for external services
- Response caching
- Database connection pooling
- Automatic retries for failed jobs

### Scaled Deployment Additional Features:
- 3 API server instances (48 total workers)
- Nginx load balancer with health checks
- Request distribution across instances
- Enhanced caching at load balancer level
- Prometheus metrics endpoint
- Better fault tolerance and high availability
- Support for ~3x more concurrent requests

## Service Management

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f api-server
```

### Stop Services

```bash
docker compose down
```

### Restart Services

```bash
docker compose restart
```

### Update Services

```bash
git pull
docker compose build
docker compose up -d
```

## Monitoring (Scaled Deployment)

### Real-time Request Monitoring

```bash
python3 monitor.py
```

This shows:
- Requests per second
- Response times
- Status code distribution
- Top endpoints

### View Load Balancer Logs

```bash
docker logs -f kodosumi-nginx
```

### Check API Metrics

```bash
curl http://localhost/metrics
```

Returns Prometheus-format metrics including:
- Request counts by endpoint
- Response times
- Error rates
- Active connections

## Troubleshooting

### Docker Compose Not Found

If you get "docker: 'compose' is not a docker command", you need to install Docker Compose v2:

```bash
./install-docker.sh
```

### Permission Denied

If you get permission errors, make sure your user is in the docker group:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

### Port Already in Use

If ports are already in use, you can change them in the `.env` file or stop conflicting services.

### Database Connection Issues

Ensure the PostgreSQL container is healthy:

```bash
docker compose ps postgres
```

If issues persist, check the database logs:

```bash
docker compose logs postgres
```

## Security Considerations

1. **Change default passwords** in the `.env` file
2. **Use HTTPS** in production by setting up a reverse proxy (nginx, traefik)
3. **Restrict port access** using firewall rules
4. **Regular backups** of the PostgreSQL database
5. **Keep Docker and dependencies updated**

## Backup and Restore

### Backup Database

```bash
docker compose exec postgres pg_dump -U kodosumi_user kodosumi_db > backup.sql
```

### Restore Database

```bash
docker compose exec -T postgres psql -U kodosumi_user kodosumi_db < backup.sql
```