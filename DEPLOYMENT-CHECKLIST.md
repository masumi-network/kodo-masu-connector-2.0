# Deployment Checklist for New Instance

## Prerequisites
- Ubuntu 20.04+ or similar Linux distribution
- 4GB RAM minimum (8GB for scaled deployment)
- 20GB free disk space
- Root or sudo access

## Step-by-Step Deployment

### 1. Clone Repository
```bash
git clone <repository-url>
cd kodo-masu-connector-2.0
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your specific values:
# - KODOSUMI_USERNAME (your Kodosumi account)
# - KODOSUMI_PASSWORD (your Kodosumi password)  
# - PAYMENT_API_KEY (your Masumi payment API key)
# - PAYMENT_SERVICE_URL (your payment service URL)
# - Database credentials (can keep defaults for new deployment)
```

### 3. Install Docker (if needed)
```bash
# The script will check and install Docker if not present
bash install-docker.sh
```

### 4. Deploy Services

#### Option A: Single Instance Deployment
```bash
bash startup.sh
```

#### Option B: Scaled Production Deployment  
```bash
bash deploy-scaled.sh
```

### 5. Verify Deployment
```bash
# Check all services are running
docker ps

# Check database migrations were applied
docker exec kodosumi-postgres psql -U kodosumi_user -d kodosumi_db -c "\dt"
# Should show: flows, jobs, cron_executions tables

# Check API health
curl http://localhost:8000/health

# Check admin dashboard
# Open browser to http://your-server-ip:5000
```

## What's Included

### Core Features
- MIP-003 compliant API endpoints
- Automatic payment verification
- Kodosumi workflow integration  
- Database-driven configuration
- Admin dashboard for monitoring

### Recent Updates (July 2025)
- **Cron Execution Tracking**: All scheduled tasks now log their executions to database
- **Enhanced Monitoring**: Admin dashboard shows real-time cron job status with:
  - Execution counts
  - Success/failure rates
  - Last run times
  - Next scheduled runs
  - Average execution duration
- **Payment Service Configuration**: Payment completion URLs now stored in database
- **Fixed Dependencies**: All services include required Python packages

### Service Schedule
- **Authenticator**: Every 10 hours (API key refresh)
- **Flow Sync**: Every 30 minutes (workflow catalog update)
- **Payment Checker**: Every 2 minutes (payment verification)
- **Kodosumi Starter**: Every minute (job submission)
- **Kodosumi Status**: Every 2 minutes (job completion check)

## Troubleshooting

### Check Service Logs
```bash
docker logs kodosumi-<service-name>
# e.g., docker logs kodosumi-api-server
```

### Check Cron Executions
```bash
docker exec kodosumi-postgres psql -U kodosumi_user -d kodosumi_db \
  -c "SELECT service_name, MAX(execution_time) as last_run FROM cron_executions GROUP BY service_name;"
```

### Reset Everything
```bash
docker compose down -v  # Warning: Deletes all data
bash startup.sh         # Fresh start
```

## Post-Deployment

1. Access admin dashboard at `http://your-server-ip:5000`
2. Monitor cron jobs section for service health
3. Check flows are synced from Kodosumi
4. Test API endpoints with your MIP-003 client

## Security Notes
- Change default PostgreSQL password in production
- Use HTTPS reverse proxy for public deployment
- Restrict admin dashboard access (port 5000) with firewall rules
- Keep PAYMENT_API_KEY secure