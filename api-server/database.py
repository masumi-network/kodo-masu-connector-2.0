import asyncio
import asyncpg
from typing import Optional, Dict, Any, List
import json
import logging
from config import config
from cache import flow_cache

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Manages database connections and operations."""
    
    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
    
    async def initialize(self):
        """Initialize the database connection pool."""
        try:
            self._pool = await asyncpg.create_pool(
                config.database_url,
                min_size=5,       # Reduced for multiple instances
                max_size=15,      # Max 15 per instance x 3 instances = 45 connections
                max_queries=50000,  # Higher query limit
                max_inactive_connection_lifetime=300,
                command_timeout=60,
                server_settings={
                    'jit': 'off'  # Disable JIT for more predictable performance
                }
            )
            logger.info("Database pool initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database pool: {e}")
            raise
    
    async def close(self):
        """Close the database connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("Database pool closed")
    
    async def get_flow_by_uid(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get flow data by UID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM flows WHERE uid = $1 AND deprecated IS NOT TRUE",
                uid
            )
            if row:
                return dict(row)
            return None
    
    async def get_flow_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get flow data by summary (name)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM flows WHERE LOWER(summary) = LOWER($1) AND deprecated IS NOT TRUE",
                name
            )
            if row:
                return dict(row)
            return None
    
    async def get_flow_by_url_identifier(self, url_identifier: str) -> Optional[Dict[str, Any]]:
        """Get flow data by URL identifier."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM flows WHERE url_identifier = $1 AND deprecated IS NOT TRUE",
                url_identifier
            )
            if row:
                return dict(row)
            return None
    
    async def get_flow_by_uid_or_name(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Get flow data by UID, URL identifier, or summary (name) with caching."""
        # Check cache first
        cached_flow = await flow_cache.get(identifier)
        if cached_flow:
            return cached_flow
        
        # Try by UID first (exact match)
        flow = await self.get_flow_by_uid(identifier)
        if flow:
            await flow_cache.set(identifier, flow)
            return flow
        
        # Try by URL identifier (YouTubeChannelAnalysis)
        flow = await self.get_flow_by_url_identifier(identifier)
        if flow:
            await flow_cache.set(identifier, flow)
            return flow
        
        # Try by name if other lookups failed
        flow = await self.get_flow_by_name(identifier)
        if flow:
            await flow_cache.set(identifier, flow)
        return flow
    
    async def get_all_flows(self) -> List[Dict[str, Any]]:
        """Get all non-deprecated flows."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT uid, summary, description, author, organization, tags, url_identifier FROM flows WHERE deprecated IS NOT TRUE ORDER BY summary"
            )
            return [dict(row) for row in rows]
    
    async def create_job(self, flow_uid: str, input_data: Dict[str, Any], 
                        payment_data: Dict[str, Any], 
                        identifier_from_purchaser: str) -> str:
        """Create a new job record and return the job_id."""
        async with self._pool.acquire() as conn:
            job_id = await conn.fetchval(
                """
                INSERT INTO jobs (
                    flow_uid, input_data, payment_data, 
                    identifier_from_purchaser, status, input_hash
                ) VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING job_id
                """,
                flow_uid,
                json.dumps(input_data),
                json.dumps(payment_data),
                identifier_from_purchaser,
                "awaiting_payment",
                payment_data.get("input_hash")
            )
            # Convert UUID to string since asyncpg returns UUID objects
            return str(job_id)
    
    async def get_job_by_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job data by job_id."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM jobs WHERE job_id = $1",
                job_id
            )
            if row:
                job_data = dict(row)
                # Parse JSON fields
                if job_data.get('input_data'):
                    job_data['input_data'] = json.loads(job_data['input_data'])
                if job_data.get('payment_data'):
                    job_data['payment_data'] = json.loads(job_data['payment_data'])
                if job_data.get('result'):
                    job_data['result'] = json.loads(job_data['result'])
                return job_data
            return None
    
    async def update_job_status(self, job_id: str, status: str, 
                               result: Optional[Dict[str, Any]] = None,
                               message: Optional[str] = None,
                               reasoning: Optional[str] = None):
        """Update job status and optionally result, message, and reasoning."""
        async with self._pool.acquire() as conn:
            if result:
                await conn.execute(
                    """
                    UPDATE jobs 
                    SET status = $1, result = $2, message = $3, reasoning = $4, updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = $5
                    """,
                    status, json.dumps(result), message, reasoning, job_id
                )
            else:
                await conn.execute(
                    """
                    UPDATE jobs 
                    SET status = $1, message = $2, reasoning = $3, updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = $4
                    """,
                    status, message, reasoning, job_id
                )

    async def get_pool_stats(self) -> Dict[str, Any]:
        """Get database pool statistics."""
        if not self._pool:
            return {"status": "not_initialized"}
        
        return {
            "size": self._pool.get_size(),
            "idle": self._pool.get_idle_size(),
            "max_size": self._pool.get_max_size(),
            "min_size": self._pool.get_min_size()
        }

# Global database manager instance
db_manager = DatabaseManager()