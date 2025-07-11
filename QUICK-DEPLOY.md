# Quick Deployment Guide

## For New Server Deployment

```bash
# 1. Clone the repository
git clone <your-repo-url> kodo-masu-connector-2.0
cd kodo-masu-connector-2.0

# 2. Set up environment
cp .env.example .env
nano .env  # Edit with your credentials

# 3. Install Docker (if needed)
bash install-docker.sh

# 4. Deploy
bash startup.sh  # Single instance
# OR
bash deploy-scaled.sh  # Production (3 API servers + load balancer)

# 5. Verify
curl http://localhost:8000/health
# Admin Dashboard: http://your-ip:5000
```

## What's New
- ✅ Cron execution tracking in database
- ✅ Real-time monitoring in admin dashboard  
- ✅ Payment checker now runs every 2 minutes
- ✅ All Python3 compatibility issues fixed
- ✅ Database-driven payment service configuration

## Service Status Check
```bash
# View all cron executions
docker exec kodosumi-postgres psql -U kodosumi_user -d kodosumi_db \
  -c "SELECT service_name, COUNT(*), MAX(execution_time) 
      FROM cron_executions 
      GROUP BY service_name;"
```

## Access Points
- API: `http://localhost:8000`
- API Docs: `http://localhost:8000/docs`
- Admin Dashboard: `http://localhost:5000`
- pgAdmin: `http://localhost:8080` (admin@admin.com / admin)

All services are now properly logging their executions and the deployment scripts are ready for use on new instances!