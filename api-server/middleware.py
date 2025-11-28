"""
Middleware components for API robustness and monitoring.
"""
from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from prometheus_client import Counter, Histogram, Gauge, generate_latest
import time
import asyncio
from typing import Callable
import logging

logger = logging.getLogger(__name__)

# Prometheus metrics
request_count = Counter('api_requests_total', 'Total API requests', ['method', 'endpoint', 'status'])
request_duration = Histogram('api_request_duration_seconds', 'API request duration', ['method', 'endpoint'])
active_requests = Gauge('api_active_requests', 'Active API requests')
health_check_duration = Histogram('health_check_duration_seconds', 'Health check duration')

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

class RobustnessMiddleware:
    """Middleware for adding robustness features to the API."""
    
    def __init__(self, app, request_timeout: float = 30.0):
        self.app = app
        self.request_timeout = request_timeout
    
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Track active requests
            active_requests.inc()
            
            # Get request details
            request = Request(scope, receive)
            method = request.method
            path = request.url.path
            
            # Start timing
            start_time = time.time()
            
            # Handle timeout
            async def timeout_wrapper():
                try:
                    await asyncio.wait_for(
                        self.app(scope, receive, send),
                        timeout=self.request_timeout
                    )
                except asyncio.TimeoutError:
                    logger.error(f"Request timeout: {method} {path}")
                    response = JSONResponse(
                        status_code=504,
                        content={"detail": "Request timeout"}
                    )
                    await response(scope, receive, send)
                finally:
                    # Record metrics
                    duration = time.time() - start_time
                    request_duration.labels(method=method, endpoint=path).observe(duration)
                    active_requests.dec()
            
            await timeout_wrapper()
        else:
            await self.app(scope, receive, send)

class HealthCheckMiddleware:
    """Optimized middleware for health check endpoint."""
    
    def __init__(self, app):
        self.app = app
        self._health_cache = None
        self._cache_time = 0
        self._cache_duration = 1.0  # Cache for 1 second
        self._availability_cache = {}  # Cache for availability endpoints
        self._availability_cache_duration = 30.0  # Cache for 30 seconds
    
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/health":
            # Use cached response if available
            current_time = time.time()
            if self._health_cache and (current_time - self._cache_time) < self._cache_duration:
                response = Response(content=self._health_cache, media_type="application/json")
                await response(scope, receive, send)
                return
            
            # Generate new response
            start_time = time.time()
            from datetime import datetime
            response_data = {
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat()
            }
            import json
            self._health_cache = json.dumps(response_data)
            self._cache_time = current_time
            
            # Record health check duration
            health_check_duration.observe(time.time() - start_time)
            
            response = Response(content=self._health_cache, media_type="application/json")
            await response(scope, receive, send)
            return
        else:
            await self.app(scope, receive, send)

async def metrics_endpoint(request: Request):
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type="text/plain")

def setup_rate_limiting(app):
    """Setup rate limiting for the application."""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    
    # Define rate limits for different endpoints
    rate_limits = {
        "/health": "500/minute",  # Increased for monitoring
        "/flows": "30/minute",
        "/{flow_identifier}/start_job": "10/minute",
        "/{flow_identifier}/status": "60/minute",
        "/{flow_identifier}/availability": "2000/minute",  # Very high - lightweight endpoint
        "/{flow_identifier}/input_schema": "60/minute",   # Increased - cached & read-only
        "/{flow_identifier}/provide_input": "20/minute",
    }
    
    return rate_limits
