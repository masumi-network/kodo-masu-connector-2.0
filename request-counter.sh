#!/bin/bash

echo "Request Counter - Updates every 5 seconds"
echo "Press Ctrl+C to stop"
echo ""

while true; do
    clear
    echo "=== KODO-MASU CONNECTOR REQUEST STATS ==="
    echo "Time: $(date)"
    echo ""
    
    # Count total requests from all API servers
    echo "📊 REQUESTS BY API SERVER:"
    for i in 1 2 3; do
        container="kodo-masu-connector-20-api-server-$i"
        count=$(docker logs $container 2>&1 | grep -c "HTTP/1.1")
        echo "  API Server $i: $count total requests"
    done
    
    echo ""
    echo "🌐 NGINX STATS (last 1 minute):"
    nginx_total=$(docker logs kodosumi-nginx --since 1m 2>&1 | grep -c "HTTP/1.1")
    nginx_200=$(docker logs kodosumi-nginx --since 1m 2>&1 | grep -c "200")
    nginx_404=$(docker logs kodosumi-nginx --since 1m 2>&1 | grep -c "404")
    nginx_502=$(docker logs kodosumi-nginx --since 1m 2>&1 | grep -c "502")
    
    echo "  Total requests: $nginx_total"
    echo "  Success (2xx): $nginx_200"
    echo "  Not Found (404): $nginx_404"
    echo "  Bad Gateway (502): $nginx_502"
    
    echo ""
    echo "🎯 TOP REQUESTED ENDPOINTS (last 1 minute):"
    docker logs kodosumi-nginx --since 1m 2>&1 | \
        grep "HTTP/1.1" | \
        awk '{print $7}' | \
        sort | uniq -c | sort -rn | head -10
    
    sleep 5
done