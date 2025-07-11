# Monitoring Commands for Kodo-Masu Connector

## Real-time Request Monitoring

### Watch nginx logs in real-time:
```bash
docker logs -f kodosumi-nginx
```

### Count requests per second (updates every second):
```bash
watch -n 1 'docker logs kodosumi-nginx --since 10s 2>&1 | grep -c "HTTP/1.1"'
```

### Monitor specific endpoint:
```bash
docker logs -f kodosumi-nginx | grep availability
```

### Show request rate by status code:
```bash
watch -n 5 'echo "=== Last 1 minute ===" && docker logs kodosumi-nginx --since 1m 2>&1 | grep -E "HTTP/1.1\" [0-9]{3}" | awk "{print \$9}" | sort | uniq -c | sort -rn'
```

## Prometheus Metrics

### Get current active requests:
```bash
curl -s http://localhost:8000/metrics | grep "api_active_requests " | grep -v "#"
```

### Get total requests by endpoint:
```bash
curl -s http://localhost:8000/metrics | grep "api_requests_total" | grep -v "#" | sort -k2 -t'{' | tail -20
```

## Database Connections

### Check current database connections:
```bash
docker exec kodosumi-postgres psql -U kodosumi_user -d kodosumi_db -c "SELECT count(*) FROM pg_stat_activity WHERE datname = 'kodosumi_db';"
```

### Show active queries:
```bash
docker exec kodosumi-postgres psql -U kodosumi_user -d kodosumi_db -c "SELECT pid, now() - pg_stat_activity.query_start AS duration, query FROM pg_stat_activity WHERE (now() - pg_stat_activity.query_start) > interval '5 minutes';"
```

## Container Resource Usage

### Real-time container stats:
```bash
docker stats --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
```

### API server specific stats:
```bash
docker stats --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}" $(docker ps --filter "name=api-server" -q)
```

## Load Testing

### Simple load test on availability endpoint:
```bash
# 100 concurrent requests
ab -n 1000 -c 100 http://localhost:8000/availability

# Or using curl in a loop
for i in {1..100}; do curl -s http://localhost:8000/availability & done; wait
```

### Monitor while load testing:
```bash
# Terminal 1: Run load test
while true; do for i in {1..50}; do curl -s http://localhost:8000/youtube_analysis/availability & done; wait; sleep 1; done

# Terminal 2: Monitor
./request-counter.sh
```