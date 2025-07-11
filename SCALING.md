# Scaling Kodo-Masu Connector for High Availability

This guide describes how to scale the Kodo-Masu Connector to handle high loads, especially on the `/availability` endpoints.

## Scaling Improvements Implemented

### 1. Increased Worker Count
- Increased from 8 to 16 workers per API server instance
- Added uvloop for better async performance
- Set concurrency limit to 1000 connections

### 2. Horizontal Scaling with Load Balancing
- Created `docker-compose.scale.yml` for running multiple API server instances
- Added nginx load balancer with connection pooling
- Implemented least_conn load balancing strategy

### 3. Optimized Availability Endpoint
- Removed all database calls from `/availability` endpoints
- Returns hardcoded "available" response immediately
- Added global `/availability` endpoint with no rate limiting
- Increased rate limit to 2000 requests/minute per IP

### 4. Caching at Multiple Levels
- Nginx-level caching for availability endpoints (1 minute TTL)
- Response caching with cache warming
- Cache serves stale content during backend updates

### 5. Database Connection Pool Optimization
- Increased min connections from 10 to 20
- Increased max connections from 20 to 50
- Disabled JIT compilation for predictable performance
- Increased max queries to 100,000

## Running with Horizontal Scaling

### Basic Scaling (3 API server instances)
```bash
./startup-scaled.sh
```

### Custom Scaling
```bash
# Start with 5 API server instances
docker-compose -f docker-compose.yml -f docker-compose.scale.yml up -d --scale api-server=5
```

### Monitoring
```bash
# View all instances
docker-compose -f docker-compose.yml -f docker-compose.scale.yml ps

# View logs from all API servers
docker-compose -f docker-compose.yml -f docker-compose.scale.yml logs -f api-server

# View nginx load balancer logs
docker-compose -f docker-compose.yml -f docker-compose.scale.yml logs -f nginx
```

## Load Testing

To verify the scaling improvements:

```bash
# Test availability endpoint (should handle 1000+ requests/second)
ab -n 10000 -c 100 http://localhost:8000/youtube_analysis/availability

# Using wrk for sustained load
wrk -t12 -c400 -d30s http://localhost:8000/youtube_analysis/availability
```

## Further Scaling Options

If you need even more capacity:

1. **Increase API server replicas**: Edit `startup-scaled.sh` and change `--scale api-server=3` to a higher number

2. **Use external load balancer**: Deploy behind AWS ALB, Cloudflare, or other CDN/load balancer

3. **Database read replicas**: For read-heavy endpoints, configure PostgreSQL read replicas

4. **Redis caching**: Add Redis for distributed caching across instances

5. **Kubernetes deployment**: For auto-scaling based on load, deploy to Kubernetes with HPA

## Monitoring Recommendations

1. **Prometheus metrics**: Available at `/metrics` endpoint
2. **Health checks**: `/health` for basic, `/health/detailed` for component status
3. **Nginx cache stats**: Check `X-Cache-Status` header in responses

## Troubleshooting

### High Memory Usage
- Reduce worker count in Dockerfile
- Lower database pool max_size
- Decrease nginx worker_connections

### Database Connection Exhaustion
- Increase PostgreSQL max_connections
- Optimize queries to release connections faster
- Add pgbouncer for connection pooling

### Slow Response Times
- Check nginx error logs for upstream timeouts
- Monitor database query performance
- Verify all instances are healthy