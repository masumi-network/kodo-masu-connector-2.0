#!/usr/bin/env python3
"""
Kodosumi Job Status Checker Service

This microservice runs every 2 minutes and:
1. Checks for jobs that are running in Kodosumi (have a kodosumi_fid)
2. Queries Kodosumi for their status
3. Updates the job when it's completed with the final result
4. Submits results to Masumi for payment completion
"""
import asyncio
import asyncpg
import httpx
import logging
import os
import json
import re
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from html import unescape
from dotenv import load_dotenv
from database_payment_service import DatabasePaymentService

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR_CANDIDATES = [
    os.path.join(SCRIPT_DIR, 'shared'),
    os.path.join(SCRIPT_DIR, '..', 'shared')
]
for shared_path in SHARED_DIR_CANDIDATES:
    if os.path.isdir(shared_path) and shared_path not in sys.path:
        sys.path.append(shared_path)

from mip003_converter import convert_kodosumi_to_mip003  # noqa: E402
from status_messages import WORKING_STATUS_MESSAGE  # noqa: E402

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Payment service is now available through database configuration
PAYMENT_SERVICE_AVAILABLE = True

RUNNING_MESSAGE = "The agent is now working on your task. Please check back soon."

# Retry configuration
MAX_TIMEOUT_ATTEMPTS = 20
MAX_NOT_FOUND_ATTEMPTS = 20
FINAL_RETRY_DELAY = timedelta(minutes=30)
MAX_STREAM_BYTES = 65536
MAX_STREAM_DURATION = 2.0
STREAM_TIMESTAMP_RE = re.compile(r'^\d+(?:\.\d+)?:')
HTML_TAG_RE = re.compile(r'<[^>]+>')

class KodosumiStatusChecker:
    """Service for checking status of jobs in Kodosumi."""
    
    def __init__(self):
        # Database configuration
        self.database_url = self._build_database_url()
        
        # Kodosumi configuration
        self.kodosumi_server_url = os.getenv('KODOSUMI_SERVER_URL', 'http://localhost:3370')
        
    def _build_database_url(self) -> str:
        """Build PostgreSQL connection URL from environment variables."""
        host = os.getenv('POSTGRES_HOST', 'postgres')
        port = os.getenv('POSTGRES_PORT', '5432')
        database = os.getenv('POSTGRES_DB')
        user = os.getenv('POSTGRES_USER')
        password = os.getenv('POSTGRES_PASSWORD')
        
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"
    
    @staticmethod
    def _strip_stream_prefix(value: str) -> str:
        """Remove SSE timestamp or helper prefixes from a data line."""
        if not value:
            return value
        match = STREAM_TIMESTAMP_RE.match(value)
        if match:
            value = value[match.end():]
        # Remove helper prefixes like "dict:" that appear before JSON payloads
        if value.startswith('dict:'):
            return value[5:]
        if value.startswith('list:'):
            return value[5:]
        return value

    @staticmethod
    def _clean_stream_text(fragment: str) -> Optional[str]:
        """Convert HTML-ish fragments from the SSE stream into readable text."""
        if not fragment:
            return None
        text = fragment
        replacements = {
            '<br />': '\n',
            '<br/>': '\n',
            '<br>': '\n',
            '</p>': '\n',
            '<p>': '\n',
            '</div>': '\n',
            '<div>': '\n',
            '</ul>': '\n',
            '<ul>': '\n',
            '</ol>': '\n',
            '<ol>': '\n',
        }
        for needle, replacement in replacements.items():
            text = text.replace(needle, replacement)
        text = text.replace('<li>', '\n- ')
        text = text.replace('</li>', '')
        text = text.replace('<strong>', '')
        text = text.replace('</strong>', '')
        text = text.replace('<em>', '')
        text = text.replace('</em>', '')
        text = unescape(text)
        text = HTML_TAG_RE.sub('', text)
        text = text.replace('\xa0', ' ')
        lines = [line.strip() for line in text.splitlines()]
        cleaned = '\n'.join(line for line in lines if line)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        cleaned = cleaned.strip()
        return cleaned or None

    def _extract_latest_stream_message(self, raw_stream: str) -> Optional[str]:
        """Parse the SSE stream and return the latest progress message."""
        if not raw_stream:
            return None
        normalized = raw_stream.replace('\r\n', '\n')
        blocks: List[List[str]] = []
        current: List[str] = []
        for line in normalized.split('\n'):
            if not line.strip():
                if current:
                    blocks.append(current)
                    current = []
                continue
            current.append(line)
        if current:
            blocks.append(current)
        latest_message: Optional[str] = None
        for block in blocks:
            event_type = None
            data_lines: List[str] = []
            for line in block:
                if line.startswith('event:'):
                    event_type = line.split(':', 1)[1].strip()
                elif line.startswith('data:'):
                    payload = line.split(':', 1)[1]
                    data_lines.append(payload.lstrip())
            if event_type == 'result' and data_lines:
                cleaned_lines = [self._strip_stream_prefix(dl) for dl in data_lines]
                combined = '\n'.join(cleaned_lines).strip()
                cleaned = self._clean_stream_text(combined)
                if cleaned:
                    latest_message = cleaned
        return latest_message

    async def fetch_latest_progress_message(self, fid: str, api_key: str) -> Optional[str]:
        """Download the main event stream and extract the latest progress snippet."""
        stream_url = f"{self.kodosumi_server_url.rstrip('/')}/outputs/main/{fid}"
        try:
            timeout = httpx.Timeout(10.0, read=3.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    'GET',
                    stream_url,
                    headers={'KODOSUMI_API_KEY': api_key},
                    timeout=timeout,
                ) as response:
                    if response.status_code != 200:
                        logger.warning(
                            f"Failed to fetch progress stream for fid {fid}: HTTP {response.status_code}"
                        )
                        return None
                    chunks: List[str] = []
                    bytes_read = 0
                    loop = asyncio.get_running_loop()
                    start_time = loop.time()
                    async for chunk in response.aiter_text():
                        if chunk:
                            chunks.append(chunk)
                            bytes_read += len(chunk)
                        if bytes_read >= MAX_STREAM_BYTES:
                            break
                        if loop.time() - start_time >= MAX_STREAM_DURATION:
                            break
            if not chunks:
                return None
            return self._extract_latest_stream_message(''.join(chunks))
        except httpx.HTTPError as exc:
            logger.warning(f"Error fetching progress stream for fid {fid}: {exc}")
            return None

    async def update_running_job_progress(
        self,
        conn: asyncpg.Connection,
        job: Dict[str, Any],
        api_key: str,
    ) -> bool:
        """Ensure running jobs show a consistent user-friendly message without extra queries."""
        current_message = (job.get('message') or '').strip()
        if current_message == WORKING_STATUS_MESSAGE:
            return False
        try:
            result = await conn.execute(
                """
                UPDATE jobs
                SET message = $2,
                    current_status_id = gen_random_uuid(),
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $1::uuid
                """,
                str(job['job_id']),
                WORKING_STATUS_MESSAGE,
            )
            if result == "UPDATE 1":
                logger.info(
                    f"Set running job {job['job_id']} message to standard working text"
                )
                return True
            logger.warning(f"Progress update for job {job['job_id']} did not modify any rows")
            return False
        except Exception as exc:
            logger.error(f"Failed to update progress message for job {job['job_id']}: {exc}")
            return False
    
    async def get_latest_api_key(self, conn: asyncpg.Connection) -> Optional[str]:
        """Backward-compatible wrapper returning only the API key."""
        api_key, _ = await self.get_latest_credentials(conn)
        return api_key

    async def get_latest_credentials(self, conn: asyncpg.Connection) -> Tuple[Optional[str], Optional[str]]:
        """Get the latest API key and session cookie from the database."""
        try:
            query = """
                SELECT api_key, session_cookie FROM api_keys 
                ORDER BY created_at DESC 
                LIMIT 1
            """
            row = await conn.fetchrow(query)
            if row:
                api_key = row['api_key']
                session_cookie = row['session_cookie'] if 'session_cookie' in row else None
                return api_key, session_cookie
            return None, None
        except Exception as e:
            logger.error(f"Failed to get latest API key: {e}")
            return None, None
    
    async def get_running_jobs_with_fid(self, conn: asyncpg.Connection) -> List[Dict[str, Any]]:
        """Get all jobs that are running and have a Kodosumi FID."""
        query = f"""
            SELECT j.job_id, j.flow_uid, j.status, 
                   j.result->>'kodosumi_fid' as kodosumi_fid,
                   j.result,
                   j.payment_data, j.input_data, j.identifier_from_purchaser,
                   j.agent_identifier_used, j.payment_required,
                   j.created_at, j.updated_at, j.timeout_attempts, j.not_found_attempts,
                   j.message,
                   f.summary as flow_summary,
                   COALESCE(f.agent_identifier_default, f.agent_identifier) AS flow_agent_identifier
            FROM jobs j
            LEFT JOIN flows f ON j.flow_uid = f.uid
            WHERE j.status IN ('running', 'awaiting_input')
              AND j.result->>'kodosumi_fid' IS NOT NULL
              AND j.timeout_attempts <= {MAX_TIMEOUT_ATTEMPTS}
              AND j.not_found_attempts <= {MAX_NOT_FOUND_ATTEMPTS}
            ORDER BY j.created_at ASC
        """
        
        rows = await conn.fetch(query)
        jobs = []
        
        for row in rows:
            # Parse JSON fields
            payment_data = json.loads(row['payment_data']) if row['payment_data'] else {}
            input_data = json.loads(row['input_data']) if row['input_data'] else {}
            result_data = json.loads(row['result']) if row['result'] else {}
            
            jobs.append({
                'job_id': row['job_id'],
                'flow_uid': row['flow_uid'],
                'status': row['status'],
                'kodosumi_fid': row['kodosumi_fid'],
                'result': result_data,
                'flow_summary': row['flow_summary'],
                'payment_data': payment_data,
                'input_data': input_data,
                'identifier_from_purchaser': row['identifier_from_purchaser'],
                'agent_identifier': row['agent_identifier_used'] or row['flow_agent_identifier'],
                'payment_required': row['payment_required'] if row['payment_required'] is not None else True,
                'created_at': row['created_at'],
                'updated_at': row['updated_at'],
                'timeout_attempts': row['timeout_attempts'],
                'not_found_attempts': row['not_found_attempts'],
                'message': row['message']
            })
        
        return jobs
    
    async def check_job_status(self, fid: str, api_key: str, session_cookie: Optional[str]) -> Optional[Dict[str, Any]]:
        """Check the status of a job in Kodosumi."""
        try:
            # Construct the status URL (removed extended=true as it causes timeouts)
            status_url = f"{self.kodosumi_server_url}/outputs/status/{fid}"
            
            logger.info(f"Checking status for FID {fid} at: {status_url}")
            
            # Make the request to Kodosumi using the API key
            cookies = {'kodosumi_jwt': session_cookie} if session_cookie else None

            async with httpx.AsyncClient() as client:
                headers = {
                    'KODOSUMI_API_KEY': api_key,
                    'Accept': 'application/json'
                }
                
                response = await client.get(
                    status_url,
                    headers=headers,
                    cookies=cookies,
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"Status for FID {fid}: {result.get('status', 'unknown')}")
                    return result
                elif response.status_code == 404:
                    # Job not found in Kodosumi - might not be indexed yet
                    logger.warning(f"Job FID {fid} not found in Kodosumi (404) - might not be indexed yet")
                    return {"error": "not_found", "status_code": 404}
                elif response.status_code in [400, 401, 403]:
                    # Client errors - likely won't resolve with retries
                    logger.error(f"Client error for FID {fid}. Status: {response.status_code}, Response: {response.text}")
                    return {"error": "client_error", "status_code": response.status_code}
                else:
                    # Server errors (5xx) or other - might be temporary
                    logger.error(f"Failed to get status for FID {fid}. Status: {response.status_code}, Response: {response.text}")
                    return None
                    
        except httpx.ConnectError:
            logger.warning(f"Cannot connect to Kodosumi server for FID {fid}")
            return None
        except httpx.TimeoutException:
            logger.warning(f"Timeout while checking status for FID {fid}")
            return {"timeout": True}
        except Exception as e:
            logger.error(f"Error checking status for FID {fid}: {e}")
            return None
    
    async def update_completed_job(self, conn: asyncpg.Connection, job_id: str, final_result: str) -> bool:
        """Update a job that has completed in Kodosumi."""
        try:
            sanitized_result = final_result.replace('\x00', '') if final_result else final_result
            if sanitized_result != final_result:
                logger.warning(f"Removed null bytes from final result for job {job_id}")
            final_result = sanitized_result
            logger.debug(f"Final result preview for job {job_id}: {final_result.encode('unicode_escape')[:200]}")
            # Parse the final result to store it properly
            try:
                final_data = json.loads(final_result)
            except json.JSONDecodeError:
                # If it's not valid JSON, store it as a string in a wrapper object
                final_data = {"raw_result": final_result}

            cleaned_data, removed = self._strip_null_bytes(final_data)
            if removed:
                logger.warning(f"Removed {removed} null byte occurrences while storing job {job_id} result")
            final_data = cleaned_data
            
            query = """
                UPDATE jobs 
                SET status = 'completed',
                    result = COALESCE(result, '{}'::jsonb) || $1::jsonb,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $2::uuid
            """
            
            # Merge the final result with existing result data (keeping kodosumi_fid)
            result_update = json.dumps({"final_result": final_data})
            
            result = await conn.execute(query, result_update, job_id)
            
            if result == "UPDATE 1":
                logger.info(f"Successfully updated completed job {job_id}")
                return True
            else:
                logger.warning(f"No rows updated for job {job_id}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to update completed job {job_id}: {e}")
            return False
    
    async def increment_timeout_attempts(self, conn: asyncpg.Connection, job_id: str) -> bool:
        """Increment timeout attempts for a job."""
        try:
            query = """
                UPDATE jobs 
                SET timeout_attempts = timeout_attempts + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $1::uuid
            """
            
            result = await conn.execute(query, job_id)
            
            if result == "UPDATE 1":
                logger.info(f"Incremented timeout attempts for job {job_id}")
                return True
            else:
                logger.warning(f"No rows updated for job {job_id}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to increment timeout attempts for job {job_id}: {e}")
            return False
    
    async def increment_not_found_attempts(self, conn: asyncpg.Connection, job_id: str) -> bool:
        """Increment not found (404) attempts for a job."""
        try:
            query = """
                UPDATE jobs 
                SET not_found_attempts = not_found_attempts + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $1::uuid
            """
            
            result = await conn.execute(query, job_id)
            
            if result == "UPDATE 1":
                logger.info(f"Incremented not_found_attempts for job {job_id}")
                return True
            else:
                logger.warning(f"No rows updated for job {job_id}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to increment not_found_attempts for job {job_id}: {e}")
            return False
    
    def extract_blockchain_identifier(self, payment_data: Dict[str, Any]) -> Optional[str]:
        """Extract blockchain identifier from payment data."""
        # Check nested structure first (new format)
        if 'data' in payment_data and isinstance(payment_data['data'], dict):
            return payment_data['data'].get('blockchainIdentifier')
        
        # Fallback to direct access (old format)
        return payment_data.get('blockchainIdentifier')

    async def fetch_lock_schema(
        self,
        fid: str,
        lid: str,
        api_key: str,
        session_cookie: Optional[str] = None
    ) -> Optional[List[Dict[str, Any]]]:
        """Fetch the lock schema (form elements) for a given lock."""
        url = f"{self.kodosumi_server_url}/lock/{fid}/{lid}"
        cookies = {'kodosumi_jwt': session_cookie} if session_cookie else None

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers={'KODOSUMI_API_KEY': api_key},
                    cookies=cookies,
                    timeout=30.0
                )
                response.raise_for_status()
                schema = response.json()
                logger.info(f"Fetched lock schema for fid={fid}, lid={lid}")
                return schema
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"Failed to fetch lock schema for fid={fid}, lid={lid}: "
                f"HTTP {exc.response.status_code} - {exc.response.text}"
            )
        except Exception as exc:
            logger.error(f"Error fetching lock schema for fid={fid}, lid={lid}: {exc}")
        return None

    def extract_awaiting_message(
        self,
        status_payload: Dict[str, Any],
        _depth: int = 0,
        _max_depth: int = 4
    ) -> Optional[str]:
        """Recursively extract a human-facing message from an awaiting status payload."""
        if not isinstance(status_payload, dict) or _depth > _max_depth:
            return None

        candidate_keys = [
            "message",
            "status_message",
            "statusMessage",
            "detail",
            "details",
            "prompt",
            "instructions",
            "human_message",
            "humanMessage"
        ]

        for key in candidate_keys:
            value = status_payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                joined = " ".join(str(item).strip() for item in value if isinstance(item, (str, int, float)))
                if joined.strip():
                    return joined.strip()

        for key, value in status_payload.items():
            if isinstance(value, dict):
                nested = self.extract_awaiting_message(value, _depth + 1, _max_depth)
                if nested:
                    return nested
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        nested = self.extract_awaiting_message(item, _depth + 1, _max_depth)
                        if nested:
                            return nested
                    elif isinstance(item, str) and item.strip() and "message" in key.lower():
                        return item.strip()

        return None

    async def mark_job_awaiting_input(
        self,
        conn: asyncpg.Connection,
        job_id: str,
        locks: List[Dict[str, Any]],
        message: Optional[str] = None,
        status_id: Optional[str] = None,
    ) -> None:
        """Update the job to awaiting_input state and persist lock metadata."""
        provided_message = message.strip() if isinstance(message, str) else ""
        normalized_message = provided_message or "Awaiting human input in Kodosumi"
        lock_payload: Dict[str, Any] = {"locks": locks}
        if normalized_message:
            lock_payload["message"] = normalized_message
        try:
            await conn.execute(
                """
                UPDATE jobs
                SET status = 'awaiting_input',
                    message = $3,
                    awaiting_input_status_id = COALESCE($4::uuid, awaiting_input_status_id, current_status_id),
                    current_status_id = COALESCE($4::uuid, current_status_id),
                    result = COALESCE(result, '{}'::jsonb) || $2::jsonb,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $1::uuid
                """,
                job_id,
                json.dumps({"kodosumi_locks": lock_payload}),
                normalized_message,
                status_id,
            )
            logger.info(f"Marked job {job_id} as awaiting_input")
        except Exception as exc:
            logger.error(f"Failed to mark job {job_id} as awaiting_input: {exc}")

    async def clear_job_lock_state(
        self,
        conn: asyncpg.Connection,
        job_id: str,
        message: Optional[str] = None
    ) -> None:
        """Reset job status to running and clear stored lock metadata."""
        normalized_message = (
            message.strip() if isinstance(message, str) and message.strip() else RUNNING_MESSAGE
        )
        try:
            await conn.execute(
                """
                UPDATE jobs
                SET status = 'running',
                    message = $2,
                    result = CASE 
                        WHEN result IS NULL THEN NULL
                        ELSE result - 'kodosumi_locks'
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $1::uuid
                """,
                job_id,
                normalized_message
            )
            logger.info(f"Cleared lock state for job {job_id}")
        except Exception as exc:
            logger.error(f"Failed to clear lock state for job {job_id}: {exc}")

    def _strip_null_bytes(self, value: Any) -> Tuple[Any, int]:
        """Recursively remove null bytes from strings within nested structures."""
        removed = 0

        if isinstance(value, str):
            cleaned = value.replace('\x00', '')
            removed = len(value) - len(cleaned)
            return cleaned, removed

        if isinstance(value, list):
            cleaned_list = []
            for item in value:
                cleaned_item, item_removed = self._strip_null_bytes(item)
                removed += item_removed
                cleaned_list.append(cleaned_item)
            return cleaned_list, removed

        if isinstance(value, dict):
            cleaned_dict = {}
            for key, item in value.items():
                cleaned_item, item_removed = self._strip_null_bytes(item)
                removed += item_removed
                cleaned_dict[key] = cleaned_item
            return cleaned_dict, removed

        return value, 0

    async def get_pending_lock_request(self, conn: asyncpg.Connection, job_id: str, lock_identifier: str) -> Optional[asyncpg.Record]:
        """Fetch the latest pending lock request for a job."""
        return await conn.fetchrow(
            """
            SELECT * FROM job_input_requests
            WHERE job_id = $1::uuid
              AND lock_identifier = $2
              AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            job_id,
            lock_identifier,
        )

    async def create_lock_request(
        self,
        conn: asyncpg.Connection,
        job_id: str,
        fid: str,
        lock_identifier: str,
        schema: List[Dict[str, Any]],
    ) -> asyncpg.Record:
        """Insert a new lock request for human input."""
        schema_payload = {"elements": schema}
        mip003_schema = convert_kodosumi_to_mip003(schema_payload)
        return await conn.fetchrow(
            """
            INSERT INTO job_input_requests (
                job_id, kodosumi_fid, lock_identifier, schema_raw, schema_mip003
            )
            VALUES ($1::uuid, $2, $3, $4::jsonb, $5::jsonb)
            RETURNING *
            """,
            job_id,
            fid,
            lock_identifier,
            json.dumps(schema),
            json.dumps(mip003_schema),
        )

    async def set_job_awaiting_input(
        self,
        conn: asyncpg.Connection,
        job_id: str,
        status_id: str,
        lock_identifier: str,
        message: Optional[str] = None,
    ):
        """Mark a job as awaiting human input with a specific status identifier."""
        normalized_message = (
            message.strip()
            if isinstance(message, str) and message.strip()
            else f"Awaiting human input ({lock_identifier})"
        )
        await conn.execute(
            """
            UPDATE jobs
            SET status = 'awaiting_input',
                message = $2,
                awaiting_input_status_id = $3::uuid,
                current_status_id = $3::uuid,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = $1::uuid
            """,
            job_id,
            normalized_message,
            status_id,
        )

    async def ensure_lock_request(
        self,
        conn: asyncpg.Connection,
        job: Dict[str, Any],
        fid: str,
        lock_identifier: str,
        api_key: str,
        *,
        session_cookie: Optional[str] = None,
        schema: Optional[List[Dict[str, Any]]] = None,
        message: Optional[str] = None,
    ) -> Optional[str]:
        """Ensure there is a pending lock request for the given job and lock."""
        job_id = str(job['job_id'])
        existing = await self.get_pending_lock_request(conn, job_id, lock_identifier)
        if existing:
            status_id = str(existing['status_id'])
            await self.set_job_awaiting_input(
                conn,
                job_id,
                status_id,
                lock_identifier,
                message=message,
            )
            return status_id

        if schema is None:
            schema = await self.fetch_lock_schema(
                fid,
                lock_identifier,
                api_key,
                session_cookie,
            )
        if not schema:
            logger.error(f"Unable to fetch schema for lock {lock_identifier} (FID {fid})")
            return None

        request_row = await self.create_lock_request(conn, job_id, fid, lock_identifier, schema)
        status_id = str(request_row['status_id'])
        await self.set_job_awaiting_input(
            conn,
            job_id,
            status_id,
            lock_identifier,
            message=message,
        )
        logger.info(f"Created HITL request {status_id} for job {job_id}")
        return status_id
    
    async def submit_result_to_masumi(self, job: Dict[str, Any], final_result: str, api_key: str):
        """Submit job result to Masumi for payment completion."""
        if not job.get('payment_required', True):
            logger.info(f"Skipping payment completion for free job {job['job_id']}")
            return

        try:
            # Use the database-based payment service
            completion_response = await DatabasePaymentService.complete_payment(
                job=job,
                final_result=final_result,
                api_key=api_key
            )
            
            if completion_response:
                logger.info(f"Successfully submitted result to Masumi for job {job['job_id']}")
            
        except Exception as e:
            # Don't fail the job update if payment completion fails
            logger.error(f"Failed to submit result to Masumi for job {job['job_id']}: {e}")
            logger.error(f"This means the agent may not receive payment for this completed job!")
    
    async def process_jobs(self):
        """Main process to check status of jobs in Kodosumi."""
        jobs_processed = 0  # Track for cron execution logging
        try:
            # Connect to database
            conn = await asyncpg.connect(self.database_url)
            
            # Get latest Kodosumi API key for status checking
            kodosumi_api_key, session_cookie = await self.get_latest_credentials(conn)
            if not kodosumi_api_key:
                logger.warning("No API key found in database. Cannot check job status.")
                await conn.close()
                return
            
            logger.info(f"Using Kodosumi API key: {kodosumi_api_key[:20]}...")
            if session_cookie:
                logger.info("Session cookie available for status checks")
            else:
                logger.warning("No session cookie available; status checks may fail with 401s")
            
            # Get Masumi payment API key from environment
            payment_api_key = os.getenv('PAYMENT_API_KEY')
            if not payment_api_key:
                logger.warning("No PAYMENT_API_KEY in environment. Payment completion will be skipped.")
                payment_api_key = None
            
            # Get jobs that need status checking
            running_jobs = await self.get_running_jobs_with_fid(conn)
            
            if not running_jobs:
                logger.info("No running jobs with Kodosumi FID to check")
                await conn.close()
                return
            
            logger.info(f"Found {len(running_jobs)} running jobs to check status")
            
            # Check status for each job
            for job in running_jobs:
                job_id = str(job['job_id'])
                fid = job['kodosumi_fid']
                timeout_attempts = job.get('timeout_attempts', 0) or 0
                not_found_attempts = job.get('not_found_attempts', 0) or 0
                last_update = job.get('updated_at')

                # Determine whether we should delay further retries for this job
                now_utc = datetime.utcnow()

                if timeout_attempts >= MAX_TIMEOUT_ATTEMPTS:
                    if timeout_attempts == MAX_TIMEOUT_ATTEMPTS and last_update:
                        retry_after = last_update + FINAL_RETRY_DELAY
                        if now_utc < retry_after:
                            logger.info(
                                f"Job {job_id} hit {timeout_attempts} timeouts - waiting until "
                                f"{retry_after.isoformat()} before final retry"
                            )
                            continue
                    else:
                        # Should not happen because job is marked failed once attempts exceed max
                        logger.debug(f"Skipping job {job_id} with timeout attempts {timeout_attempts}")
                        continue

                if not_found_attempts >= MAX_NOT_FOUND_ATTEMPTS:
                    if not_found_attempts == MAX_NOT_FOUND_ATTEMPTS and last_update:
                        retry_after = last_update + FINAL_RETRY_DELAY
                        if now_utc < retry_after:
                            logger.info(
                                f"Job {job_id} hit {not_found_attempts} 404s - waiting until "
                                f"{retry_after.isoformat()} before final retry"
                            )
                            continue
                    else:
                        logger.debug(f"Skipping job {job_id} with not_found_attempts {not_found_attempts}")
                        continue

                logger.info(f"Checking status for job {job_id} (FID: {fid})")
                jobs_processed += 1  # Only count attempts where we actually make a request

                # Check job status in Kodosumi
                status_result = await self.check_job_status(fid, kodosumi_api_key, session_cookie)
                
                if status_result:
                    # Check if this was a timeout
                    if status_result.get('timeout'):
                        logger.warning(f"Job {job_id} timed out - incrementing timeout attempts")
                        await self.increment_timeout_attempts(conn, job_id)
                        timeout_attempts = (job.get('timeout_attempts', 0) or 0) + 1
                        if timeout_attempts >= MAX_TIMEOUT_ATTEMPTS + 1:
                            logger.error(
                                f"Job {job_id} has timed out {timeout_attempts} times - marking as failed"
                            )
                            # Mark job as failed after 3 timeouts
                            await conn.execute(
                                "UPDATE jobs SET status = 'failed', message = $1, updated_at = CURRENT_TIMESTAMP WHERE job_id = $2::uuid",
                                f"Job timed out {timeout_attempts} times - likely never started in Kodosumi",
                                job_id
                            )
                            jobs_processed += 1
                        elif timeout_attempts == MAX_TIMEOUT_ATTEMPTS:
                            logger.warning(
                                f"Job {job_id} reached {MAX_TIMEOUT_ATTEMPTS} timeouts - scheduling final retry after "
                                f"{FINAL_RETRY_DELAY}"
                            )
                        else:
                            logger.info(
                                f"Job {job_id} timed out (attempt {timeout_attempts}/{MAX_TIMEOUT_ATTEMPTS}) - will retry later"
                            )
                        continue
                    
                    # Check if this was an error response
                    if status_result.get('error'):
                        error_type = status_result.get('error')
                        status_code = status_result.get('status_code')
                        
                        if error_type == 'not_found':
                            # Job not found in Kodosumi - increment attempts and retry
                            logger.warning(f"Job {job_id} not found in Kodosumi (404) - incrementing not_found_attempts")
                            await self.increment_not_found_attempts(conn, job_id)
                            not_found_attempts = (job.get('not_found_attempts', 0) or 0) + 1
                            if not_found_attempts >= MAX_NOT_FOUND_ATTEMPTS + 1:
                                logger.error(
                                    f"Job {job_id} not found after {not_found_attempts} attempts - marking as failed"
                                )
                                # Mark job as failed after 3 404 attempts
                                await conn.execute(
                                    "UPDATE jobs SET status = 'failed', message = $1, updated_at = CURRENT_TIMESTAMP WHERE job_id = $2::uuid",
                                    f"Job not found in Kodosumi after {not_found_attempts} attempts (HTTP 404)",
                                    job_id
                                )
                                jobs_processed += 1
                            else:
                                if not_found_attempts == MAX_NOT_FOUND_ATTEMPTS:
                                    logger.warning(
                                        f"Job {job_id} reached {MAX_NOT_FOUND_ATTEMPTS} not_found attempts - scheduling final retry after "
                                        f"{FINAL_RETRY_DELAY}"
                                    )
                                else:
                                    logger.info(
                                        f"Job {job_id} not found (attempt {not_found_attempts}/{MAX_NOT_FOUND_ATTEMPTS}) - will retry later"
                                    )
                            continue
                        elif error_type == 'client_error':
                            # Client error - also mark as failed as it won't resolve
                            logger.error(f"Job {job_id} failed with client error {status_code} - marking as failed")
                            await conn.execute(
                                "UPDATE jobs SET status = 'failed', message = $1, updated_at = CURRENT_TIMESTAMP WHERE job_id = $2::uuid",
                                f"Kodosumi API client error (HTTP {status_code})",
                                job_id
                            )
                            jobs_processed += 1
                            continue
                    
                    status = status_result.get('status', 'unknown')
                    locks = status_result.get('locks') or []
                    awaiting_message = self.extract_awaiting_message(status_result)

                    if status in ('awaiting', 'awaiting_input'):
                        if not locks:
                            logger.info(f"Job {job_id} awaiting input but no lock identifiers provided")
                            continue

                        existing_lock_info = (
                            (job.get('result') or {})
                            .get('kodosumi_locks', {})
                            .get('locks', [])
                        )
                        existing_lock_map = {
                            lock.get('lock_id'): lock
                            for lock in existing_lock_info
                            if isinstance(lock, dict) and lock.get('lock_id')
                        }

                        lock_entries: List[Dict[str, Any]] = []
                        primary_status_id: Optional[str] = None

                        for lid in locks:
                            if not isinstance(lid, str):
                                logger.warning(f"Lock identifier {lid} is not a string for job {job_id}")
                                continue

                            existing_entry = existing_lock_map.get(lid) or {}
                            schema = existing_entry.get('schema')

                            if schema is None:
                                fetched_schema = await self.fetch_lock_schema(
                                    fid,
                                    lid,
                                    kodosumi_api_key,
                                    session_cookie,
                                )
                                if isinstance(fetched_schema, dict) and 'elements' in fetched_schema:
                                    schema = fetched_schema.get('elements')
                                else:
                                    schema = fetched_schema

                            entry: Dict[str, Any] = {"lock_id": lid}
                            if schema is not None:
                                entry["schema"] = schema

                            status_id = await self.ensure_lock_request(
                                conn,
                                job,
                                fid,
                                lid,
                                kodosumi_api_key,
                                session_cookie=session_cookie,
                                schema=schema if isinstance(schema, list) else None,
                                message=awaiting_message,
                            )
                            if status_id and primary_status_id is None:
                                primary_status_id = status_id

                            lock_entries.append(entry)

                        if lock_entries:
                            await self.mark_job_awaiting_input(
                                conn,
                                job_id,
                                lock_entries,
                                awaiting_message,
                                primary_status_id,
                            )
                            jobs_processed += 1
                        continue

                    if status == 'finished':
                        # Job is completed, update with final result
                        final_result = status_result.get('final')
                        if final_result:
                            logger.info(f"Job {job_id} completed in Kodosumi")
                            success = await self.update_completed_job(conn, job_id, final_result)
                            if success:
                                logger.info(f"Successfully updated completed job {job_id}")
                                
                                # Submit result to Masumi for payment completion
                                if PAYMENT_SERVICE_AVAILABLE and payment_api_key:
                                    await self.submit_result_to_masumi(job, final_result, payment_api_key)
                                else:
                                    logger.warning(f"Payment service not available or no payment API key - skipping payment completion for job {job_id}")
                            else:
                                logger.error(f"Failed to update completed job {job_id}")
                        else:
                            logger.warning(f"Job {job_id} marked as finished but no final result provided")
                    elif status == 'failed' or status == 'error':
                        # Job failed in Kodosumi
                        logger.warning(f"Job {job_id} failed in Kodosumi with status: {status}")
                        # Update job status to failed
                        await conn.execute(
                            "UPDATE jobs SET status = 'failed', message = $1, updated_at = CURRENT_TIMESTAMP WHERE job_id = $2::uuid",
                            f"Job failed in Kodosumi with status: {status}",
                            job_id
                        )
                    elif status == 'running':
                        if job['status'] == 'awaiting_input':
                            await self.clear_job_lock_state(conn, job_id)
                        logger.info(f"Job {job_id} is still running in Kodosumi")
                        if await self.update_running_job_progress(conn, job, kodosumi_api_key):
                            jobs_processed += 1
                    else:
                        logger.warning(f"Job {job_id} has unknown status in Kodosumi: {status}")
                else:
                    # Could not check status - will retry in next cron run
                    logger.warning(f"Could not check status for job {job_id} - will retry later")
            
            await conn.close()
            logger.info("Kodosumi status checking completed successfully")
            return jobs_processed  # Return count for cron logging
            
        except Exception as e:
            logger.error(f"Error in Kodosumi status checking: {e}")
            raise

async def main():
    """Main entry point for the Kodosumi status checker."""
    logger.info("Starting Kodosumi status checker...")
    
    checker = KodosumiStatusChecker()
    await checker.process_jobs()
    
    logger.info("Kodosumi status checker completed")

if __name__ == "__main__":
    asyncio.run(main())
