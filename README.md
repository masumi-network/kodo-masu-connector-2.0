# Kodo-Masu Connector 2.0

A robust, scalable bridge between Kodosumi AI workflows and Masumi blockchain payments, providing MIP-003 compliant endpoints for AI agents.

## Quick Start

```bash
# Install Docker (if needed)
bash install-docker.sh

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Deploy (choose one)
bash startup.sh        # Single instance deployment
bash deploy-scaled.sh  # Production deployment with load balancer
```

## Architecture Overview

### Core Services

| Service | Purpose | Schedule |
|---------|---------|----------|
| **API Server** | MIP-003 compliant REST API | Always on |
| **PostgreSQL** | Data persistence | Always on |
| **Authenticator** | Kodosumi API key management | Every 10 hours |
| **Flow Sync** | Workflow synchronization | Every 2 hours |
| **Payment Checker** | Blockchain payment verification | Every 5 minutes |
| **Kodosumi Starter** | Job submission to Kodosumi | Every 2 minutes |
| **Kodosumi Status** | Job completion monitoring | Every 2 minutes |
| **Admin Dashboard** | Web management interface | Always on |
| **Nginx** (scaled only) | Load balancer | Always on |

### Data Flow

```
User → API → Payment Verification → Kodosumi → Results → Payment Completion
         ↓                                                      ↑
    PostgreSQL ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ↓
```

## API Endpoints

For each workflow (identified by `{flow_uid}`):

- `POST /{flow_uid}/start_job` - Start a new job with payment
- `GET /{flow_uid}/status` - Check job status
- `GET /{flow_uid}/availability` - Service availability
- `GET /{flow_uid}/input_schema` - Required input format
- `GET /health` - System health check
- `GET /metrics` - Prometheus metrics (scaled deployment)

## Key Features

### Robustness
- Rate limiting (60 req/min per endpoint)
- Circuit breakers for external services
- Multi-level caching
- Automatic retries
- Request timeouts

### Scalability
- Horizontal scaling (3 instances in production)
- Load balancing with health checks
- Connection pooling
- 48 total workers in scaled deployment

### Payment Integration
- Masumi blockchain payment verification
- Automatic payment completion
- Database-based configuration
- MIP-003 compliance

### Monitoring & Observability
- Cron execution tracking
- Performance metrics per service
- Success/failure rate monitoring
- Execution history in admin dashboard

## Deployment Options

### Basic (Single Instance)
- 1 API server with 16 workers
- Suitable for development/low traffic
- ~100 concurrent users

### Scaled (Production)
- 3 API servers with 48 total workers
- Nginx load balancer
- High availability
- ~300+ concurrent users

## Access Points

| Service | Basic Deploy | Scaled Deploy |
|---------|--------------|---------------|
| API | http://localhost:8000 | http://localhost |
| API Docs | http://localhost:8000/docs | http://localhost/docs |
| Admin Dashboard | http://localhost:3001 | http://localhost:3001 |
| pgAdmin | http://localhost:5050 | http://localhost:5050 |
| Metrics | N/A | http://localhost/metrics |

## Monitoring

```bash
# Real-time request monitoring (scaled deployment)
python3 monitor.py

# View logs
docker logs -f <service-name>

# Check metrics
curl http://localhost/metrics
```

## Documentation

- [Deployment Guide](DEPLOYMENT.md) - Detailed deployment instructions
- [Deployment Summary](DEPLOYMENT-SUMMARY.md) - What's included in each deployment
- [Monitoring Commands](monitoring-commands.md) - Useful monitoring queries

## Requirements

- Ubuntu 20.04+ or similar Linux
- 4GB RAM minimum (8GB for scaled deployment)
- Docker and Docker Compose
- 20GB free disk space

## License

[Your License Here]