"""
Caching utilities for improving performance.
"""
from cachetools import TTLCache
from typing import Optional, Any, Dict
import asyncio
import hashlib
import json
import logging

logger = logging.getLogger(__name__)

class FlowCache:
    """Cache for flow data to reduce database lookups."""
    
    def __init__(self, maxsize: int = 100, ttl: float = 300.0):
        """
        Initialize flow cache.
        
        Args:
            maxsize: Maximum number of items to cache
            ttl: Time to live in seconds (default 5 minutes)
        """
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = asyncio.Lock()
    
    def _get_key(self, identifier: str) -> str:
        """Generate cache key for identifier."""
        return f"flow:{identifier.lower()}"
    
    async def get(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Get flow from cache."""
        async with self._lock:
            key = self._get_key(identifier)
            data = self._cache.get(key)
            if data:
                logger.debug(f"Cache hit for flow: {identifier}")
                return data
            logger.debug(f"Cache miss for flow: {identifier}")
            return None
    
    async def set(self, identifier: str, data: Dict[str, Any]):
        """Set flow in cache."""
        async with self._lock:
            key = self._get_key(identifier)
            self._cache[key] = data
            
            # Also cache by uid and url_identifier if available
            if 'uid' in data:
                self._cache[self._get_key(data['uid'])] = data
            if 'url_identifier' in data and data['url_identifier']:
                self._cache[self._get_key(data['url_identifier'])] = data
            if 'summary' in data:
                self._cache[self._get_key(data['summary'])] = data
            
            logger.debug(f"Cached flow: {identifier}")
    
    async def invalidate(self, identifier: str):
        """Invalidate cache entry."""
        async with self._lock:
            key = self._get_key(identifier)
            self._cache.pop(key, None)
            logger.debug(f"Invalidated cache for flow: {identifier}")
    
    async def clear(self):
        """Clear all cache entries."""
        async with self._lock:
            self._cache.clear()
            logger.info("Flow cache cleared")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "size": len(self._cache),
            "maxsize": self._cache.maxsize,
            "ttl": self._cache.ttl,
            "hits": getattr(self._cache, 'hits', 0),
            "misses": getattr(self._cache, 'misses', 0)
        }

class ResponseCache:
    """Generic response cache for expensive operations."""
    
    def __init__(self, maxsize: int = 500, ttl: float = 60.0):
        """
        Initialize response cache.
        
        Args:
            maxsize: Maximum number of items to cache
            ttl: Time to live in seconds (default 1 minute)
        """
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = asyncio.Lock()
    
    def _get_key(self, endpoint: str, params: Dict[str, Any]) -> str:
        """Generate cache key from endpoint and parameters."""
        # Sort params for consistent hashing
        sorted_params = json.dumps(params, sort_keys=True)
        key_string = f"{endpoint}:{sorted_params}"
        return hashlib.md5(key_string.encode()).hexdigest()
    
    async def get(self, endpoint: str, params: Dict[str, Any]) -> Optional[Any]:
        """Get response from cache."""
        async with self._lock:
            key = self._get_key(endpoint, params)
            return self._cache.get(key)
    
    async def set(self, endpoint: str, params: Dict[str, Any], response: Any):
        """Set response in cache."""
        async with self._lock:
            key = self._get_key(endpoint, params)
            self._cache[key] = response
    
    async def invalidate_pattern(self, endpoint_pattern: str):
        """Invalidate all cache entries matching endpoint pattern."""
        async with self._lock:
            keys_to_remove = []
            for key in self._cache:
                if endpoint_pattern in key:
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                self._cache.pop(key, None)
            
            if keys_to_remove:
                logger.info(f"Invalidated {len(keys_to_remove)} cache entries matching pattern: {endpoint_pattern}")

# Global cache instances
flow_cache = FlowCache(maxsize=100, ttl=300)  # 5 minutes TTL
response_cache = ResponseCache(maxsize=500, ttl=60)  # 1 minute TTL for most responses
schema_cache = ResponseCache(maxsize=200, ttl=600)  # 10 minutes TTL for schemas
not_found_cache = ResponseCache(maxsize=1000, ttl=300)  # 5 minutes TTL for 404s