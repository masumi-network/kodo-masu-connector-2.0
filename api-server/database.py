import asyncio
import asyncpg
from typing import Optional, Dict, Any, List, Tuple, Union
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

    async def get_latest_kodosumi_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """Fetch the most recent Kodosumi API key and session cookie."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT api_key, session_cookie
                FROM api_keys
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            if not row:
                return None, None
            return row["api_key"], row.get("session_cookie")
    
    async def create_job(self, flow_uid: str, input_data: Dict[str, Any], 
                        payment_data: Dict[str, Any], 
                        identifier_from_purchaser: str,
                        *,
                        status: str = "awaiting_payment",
                        input_hash: Optional[str] = None,
                        agent_identifier_used: Optional[str] = None,
                        payment_required: bool = True,
                        waiting_for_start: bool = False) -> str:
        """Create a new job record and return the job_id."""
        async with self._pool.acquire() as conn:
            job_id = await conn.fetchval(
                """
                INSERT INTO jobs (
                    flow_uid, input_data, payment_data,
                    identifier_from_purchaser, status, input_hash,
                    agent_identifier_used, payment_required, waiting_for_start_in_kodosumi
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING job_id
                """,
                flow_uid,
                json.dumps(input_data),
                json.dumps(payment_data or {}),
                identifier_from_purchaser,
                status,
                input_hash,
                agent_identifier_used,
                payment_required,
                waiting_for_start
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
                if job_data.get('job_id'):
                    job_data['job_id'] = str(job_data['job_id'])
                if job_data.get('input_data'):
                    job_data['input_data'] = json.loads(job_data['input_data'])
                if job_data.get('payment_data'):
                    job_data['payment_data'] = json.loads(job_data['payment_data'])
                if job_data.get('result'):
                    job_data['result'] = json.loads(job_data['result'])
                if job_data.get('current_status_id'):
                    job_data['current_status_id'] = str(job_data['current_status_id'])
                if job_data.get('awaiting_input_status_id'):
                    job_data['awaiting_input_status_id'] = str(job_data['awaiting_input_status_id'])
                return job_data
            return None
    
    async def update_job_status(
        self,
        job_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None,
        reasoning: Optional[str] = None,
        waiting_for_start: Optional[bool] = None,
        *,
        awaiting_input_status_id: Optional[str] = None,
        clear_awaiting_input: bool = False,
        new_status_id: Optional[str] = None,
    ):
        """Update job status and optionally result, message, reasoning, and waiting flag."""
        async with self._pool.acquire() as conn:
            if result is not None:
                await conn.execute(
                    """
                    UPDATE jobs 
                    SET status = $1,
                        result = $2,
                        message = $3,
                        reasoning = $4,
                        waiting_for_start_in_kodosumi = COALESCE($7, waiting_for_start_in_kodosumi),
                        awaiting_input_status_id = CASE
                            WHEN $5::uuid IS NOT NULL THEN $5::uuid
                            WHEN $6 THEN NULL
                            ELSE awaiting_input_status_id
                        END,
                        current_status_id = COALESCE($8::uuid, current_status_id),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = $9
                    """,
                    status,
                    json.dumps(result),
                    message,
                    reasoning,
                    awaiting_input_status_id,
                    clear_awaiting_input,
                    waiting_for_start,
                    new_status_id,
                    job_id,
                )
            else:
                await conn.execute(
                    """
                    UPDATE jobs 
                    SET status = $1,
                        message = $2,
                        reasoning = $3,
                        awaiting_input_status_id = CASE
                            WHEN $4::uuid IS NOT NULL THEN $4::uuid
                            WHEN $5 THEN NULL
                            ELSE awaiting_input_status_id
                        END,
                        current_status_id = COALESCE($6::uuid, current_status_id),
                        waiting_for_start_in_kodosumi = COALESCE($7, waiting_for_start_in_kodosumi),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = $8
                    """,
                    status,
                    message,
                    reasoning,
                    awaiting_input_status_id,
                    clear_awaiting_input,
                    new_status_id,
                    waiting_for_start,
                    job_id,
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

    async def get_latest_api_key(self) -> Optional[str]:
        """Fetch the latest stored Kodosumi API key."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT api_key FROM api_keys ORDER BY created_at DESC LIMIT 1"
            )
            return row['api_key'] if row else None

    async def get_pending_input_request(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return the most recent pending input request for a job."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM job_input_requests
                WHERE job_id = $1
                  AND status IN ('pending', 'awaiting', 'awaiting_input')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                job_id,
            )
            return self._parse_input_request(row) if row else None

    async def get_input_request_by_status_id(self, status_id: str) -> Optional[Dict[str, Any]]:
        """Fetch an input request by its status identifier."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM job_input_requests WHERE status_id = $1",
                status_id,
            )
            return self._parse_input_request(row) if row else None

    async def create_input_request(
        self,
        job_id: str,
        kodosumi_fid: str,
        lock_identifier: str,
        schema_raw: Optional[Union[Dict[str, Any], List[Any]]],
        schema_mip003: Optional[List[Dict[str, Any]]],
        *,
        status_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new human-in-the-loop input request."""
        async with self._pool.acquire() as conn:
            if status_id:
                query = """
                    INSERT INTO job_input_requests (
                        status_id, job_id, kodosumi_fid, lock_identifier, schema_raw, schema_mip003
                    )
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb, $6::jsonb)
                    RETURNING *
                """
                params = (
                    status_id,
                    job_id,
                    kodosumi_fid,
                    lock_identifier,
                    json.dumps(schema_raw) if schema_raw is not None else None,
                    json.dumps(schema_mip003) if schema_mip003 is not None else None,
                )
            else:
                query = """
                    INSERT INTO job_input_requests (
                        job_id, kodosumi_fid, lock_identifier, schema_raw, schema_mip003
                    )
                    VALUES ($1::uuid, $2, $3, $4::jsonb, $5::jsonb)
                    RETURNING *
                """
                params = (
                    job_id,
                    kodosumi_fid,
                    lock_identifier,
                    json.dumps(schema_raw) if schema_raw is not None else None,
                    json.dumps(schema_mip003) if schema_mip003 is not None else None,
                )
            row = await conn.fetchrow(query, *params)
            return self._parse_input_request(row)

    async def ensure_job_awaiting_input_status(self, job_id: str, status_id: str):
        """Ensure the jobs table tracks the provided awaiting input status identifier."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jobs
                SET awaiting_input_status_id = $2::uuid,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $1::uuid
                  AND (awaiting_input_status_id IS DISTINCT FROM $2::uuid)
                """,
                job_id,
                status_id,
            )

    async def update_input_request_status(
        self,
        status_id: str,
        *,
        new_status: str,
        response_data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ):
        """Mark an input request as completed or errored."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE job_input_requests
                SET status = $2,
                    response_data = COALESCE($3, response_data),
                    error_message = COALESCE($4, error_message),
                    updated_at = CURRENT_TIMESTAMP
                WHERE status_id = $1
                """,
                status_id,
                new_status,
                json.dumps(response_data) if response_data is not None else None,
                error_message,
            )

    async def get_job_status_history(self, job_id: str) -> List[Dict[str, Any]]:
        """Return chronological history of status identifiers for a job."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT status,
                       status_id::text AS status_id,
                       message,
                       created_at
                FROM job_status_events
                WHERE job_id = $1::uuid
                ORDER BY created_at ASC
                """,
                job_id,
            )

        history: List[Dict[str, Any]] = []
        for row in rows:
            history.append(
                {
                    "status": row["status"],
                    "status_id": row["status_id"],
                    "message": row["message"],
                    "created_at": row["created_at"],
                }
            )
        return history

    def _parse_input_request(self, row: Optional[asyncpg.Record]) -> Optional[Dict[str, Any]]:
        """Convert a raw input request row into a dict with parsed JSON."""
        if not row:
            return None
        data = dict(row)
        for field in ('schema_raw', 'schema_mip003', 'response_data'):
            if data.get(field):
                data[field] = json.loads(data[field])
        if data.get('status_id'):
            data['status_id'] = str(data['status_id'])
        return data

# Global database manager instance
db_manager = DatabaseManager()
