#!/usr/bin/env python3
"""
Real-time monitoring script for Kodo-Masu Connector API
"""
import time
import requests
import subprocess
import os
from collections import defaultdict
from datetime import datetime
import json

def clear_screen():
    os.system('clear' if os.name == 'posix' else 'cls')

def get_metrics():
    """Fetch Prometheus metrics from the API."""
    try:
        response = requests.get('http://localhost:8000/metrics', timeout=2)
        return response.text
    except:
        return None

def parse_metrics(metrics_text):
    """Parse Prometheus metrics into a structured format."""
    if not metrics_text:
        return {}
    
    metrics = defaultdict(lambda: defaultdict(float))
    
    for line in metrics_text.split('\n'):
        if line.startswith('#') or not line.strip():
            continue
            
        # Parse request totals
        if 'api_requests_total{' in line:
            try:
                parts = line.split('{')[1].split('}')
                labels = dict(label.split('=') for label in parts[0].split(','))
                value = float(parts[1].strip())
                
                endpoint = labels.get('endpoint', '').strip('"')
                method = labels.get('method', '').strip('"')
                status = labels.get('status', '').strip('"')
                
                key = f"{method} {endpoint}"
                metrics['requests'][key] += value
            except:
                pass
        
        # Parse active requests
        elif 'api_active_requests ' in line and '{' not in line:
            try:
                value = float(line.split()[-1])
                metrics['active_requests'] = value
            except:
                pass
    
    return metrics

def get_nginx_stats():
    """Get nginx access log stats for the last minute."""
    try:
        # Count requests in nginx logs
        result = subprocess.run([
            'docker', 'exec', 'kodosumi-nginx', 
            'sh', '-c', 
            'tail -1000 /var/log/nginx/access.log | wc -l'
        ], capture_output=True, text=True)
        
        return int(result.stdout.strip())
    except:
        return 0

def get_container_stats():
    """Get container resource usage."""
    try:
        result = subprocess.run([
            'docker', 'stats', '--no-stream', '--format', 
            'table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}'
        ], capture_output=True, text=True)
        
        return result.stdout
    except:
        return ""

def main():
    """Main monitoring loop."""
    print("Starting Kodo-Masu Connector Monitoring...")
    print("Press Ctrl+C to exit\n")
    
    previous_metrics = None
    
    try:
        while True:
            clear_screen()
            
            # Header
            print("=" * 80)
            print(f"KODO-MASU CONNECTOR MONITORING - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 80)
            
            # Get current metrics
            metrics_text = get_metrics()
            current_metrics = parse_metrics(metrics_text)
            
            # Active requests
            print(f"\n📊 ACTIVE REQUESTS: {int(current_metrics.get('active_requests', 0))}")
            
            # Request rates (calculate delta if we have previous metrics)
            if previous_metrics and 'requests' in current_metrics:
                print("\n📈 REQUEST RATES (last 5 seconds):")
                print("-" * 60)
                
                for endpoint, current_count in sorted(current_metrics['requests'].items()):
                    prev_count = previous_metrics['requests'].get(endpoint, 0)
                    rate = (current_count - prev_count) / 5  # requests per second
                    if rate > 0:
                        print(f"{endpoint:<45} {rate:>8.1f} req/s")
            
            # Top endpoints by total requests
            if 'requests' in current_metrics:
                print("\n🎯 TOP ENDPOINTS (total requests):")
                print("-" * 60)
                
                sorted_endpoints = sorted(
                    current_metrics['requests'].items(), 
                    key=lambda x: x[1], 
                    reverse=True
                )[:10]
                
                for endpoint, count in sorted_endpoints:
                    print(f"{endpoint:<45} {int(count):>10,}")
            
            # Nginx stats
            nginx_count = get_nginx_stats()
            print(f"\n🌐 NGINX: Last 1000 log entries count: {nginx_count}")
            
            # Container stats
            print("\n💻 CONTAINER RESOURCES:")
            print("-" * 60)
            container_stats = get_container_stats()
            
            # Filter for our containers
            for line in container_stats.split('\n'):
                if any(name in line for name in ['api-server', 'nginx', 'postgres']):
                    print(line)
            
            # Health check
            try:
                health_response = requests.get('http://localhost:8000/health', timeout=1)
                health_status = "✅ HEALTHY" if health_response.status_code == 200 else "❌ UNHEALTHY"
            except:
                health_status = "❌ UNREACHABLE"
            
            print(f"\n🏥 HEALTH STATUS: {health_status}")
            
            # Store current metrics for next iteration
            previous_metrics = current_metrics
            
            # Wait before next update
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped.")

if __name__ == "__main__":
    main()