#!/usr/bin/env python3
"""
Kodosumi Job Starter Service

This microservice runs every minute and:
1. Checks for jobs with waiting_for_start_in_kodosumi = true
2. Starts those jobs in Kodosumi using the latest API key
3. Updates the job status and stores the Kodosumi flow identifier (fid)
"""
import asyncio
import asyncpg
import httpx
import logging
import os
import json
import math
import mimetypes
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse
from dotenv import load_dotenv
from cron_logger import CronExecutionLogger

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def _load_chunk_size() -> int:
    raw_value = os.getenv("KODOSUMI_FILES_CHUNK_SIZE", str(512 * 1024))
    try:
        size = int(raw_value)
    except ValueError:
        logger.warning("Invalid KODOSUMI_FILES_CHUNK_SIZE '%s', defaulting to 524288", raw_value)
        size = 512 * 1024
    # Enforce valid bounds (1 byte .. 1 MiB)
    return max(1, min(size, 1024 * 1024))

UPLOAD_CHUNK_SIZE = _load_chunk_size()

def _load_max_attempts() -> int:
    """Fetch the maximum number of start attempts from the environment."""
    raw_value = os.getenv('KODOSUMI_START_MAX_ATTEMPTS', '10')
    try:
        parsed = int(raw_value)
    except ValueError:
        logger.warning("Invalid KODOSUMI_START_MAX_ATTEMPTS value '%s', defaulting to 10", raw_value)
        parsed = 10
    return max(1, parsed)

MAX_START_ATTEMPTS = _load_max_attempts()

class KodosumiStarter:
    """Service for starting jobs in Kodosumi."""
    
    def __init__(self):
        # Database configuration
        self.database_url = self._build_database_url()
        
        # Kodosumi configuration
        self.kodosumi_server_url = os.getenv('KODOSUMI_SERVER_URL', 'http://localhost:3370').rstrip('/')
        self.kodosumi_username = os.getenv('KODOSUMI_USERNAME')
        self.kodosumi_password = os.getenv('KODOSUMI_PASSWORD')
        
    def _build_database_url(self) -> str:
        """Build PostgreSQL connection URL from environment variables."""
        host = os.getenv('POSTGRES_HOST', 'postgres')
        port = os.getenv('POSTGRES_PORT', '5432')
        database = os.getenv('POSTGRES_DB')
        user = os.getenv('POSTGRES_USER')
        password = os.getenv('POSTGRES_PASSWORD')
        
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"
    
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
    
    async def get_jobs_waiting_for_kodosumi(self, conn: asyncpg.Connection) -> List[Dict[str, Any]]:
        """Get all jobs that are waiting to be started in Kodosumi."""
        query = """
            SELECT j.job_id, j.flow_uid, j.input_data, j.identifier_from_purchaser,
                   j.created_at, j.updated_at, j.kodosumi_start_attempts,
                   f.summary as flow_summary,
                   f.url_identifier as flow_url_identifier, f.url as flow_url
            FROM jobs j
            LEFT JOIN flows f ON j.flow_uid = f.uid
            WHERE j.status = 'running' 
              AND j.waiting_for_start_in_kodosumi = true
              AND j.kodosumi_start_attempts < $1
            ORDER BY j.created_at ASC
        """
        
        rows = await conn.fetch(query, MAX_START_ATTEMPTS)
        jobs = []
        
        for row in rows:
            # Parse JSON fields (PostgreSQL JSONB returns native Python objects)
            jobs.append({
                'job_id': row['job_id'],
                'flow_uid': row['flow_uid'],
                'input_data': row['input_data'],  # Already a dict from JSONB
                'identifier_from_purchaser': row['identifier_from_purchaser'],
                'flow_summary': row['flow_summary'],
                'flow_url_identifier': row['flow_url_identifier'],
                'flow_url': row['flow_url'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at'],
                'kodosumi_start_attempts': row['kodosumi_start_attempts'] or 0
            })
        
        return jobs
    
    async def convert_indices_to_values(self, input_data: Dict[str, Any], mip003_schema: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert multi-select indices back to their string values using the MIP003 schema."""
        logger.info(f"convert_indices_to_values called with: {input_data}")
        converted_data = input_data.copy()
        
        # Create a map of field IDs to their schemas
        field_map = {field['id']: field for field in mip003_schema}
        
        for field_id, value in input_data.items():
            if field_id in field_map and isinstance(value, list):
                field = field_map[field_id]
                # Check if this is an option field (multi-select)
                if field.get('type') == 'option':
                    logger.info(f"Processing option field {field_id} with value {value}")
                    values_list = field.get('data', {}).get('values', [])
                    if values_list:
                        # Convert indices to actual values
                        try:
                            converted_values = []
                            for idx in value:
                                if isinstance(idx, int) and 0 <= idx < len(values_list):
                                    converted_values.append(values_list[idx])
                                else:
                                    # If not a valid index, keep original value
                                    converted_values.append(idx)
                            # Kodosumi expects select fields as strings, not arrays
                            # For multi-select, we'll take the first value
                            if converted_values:
                                converted_data[field_id] = converted_values[0]
                                if len(converted_values) > 1:
                                    logger.warning(f"Field {field_id} has multiple values {converted_values}, using first: '{converted_values[0]}'")
                                else:
                                    logger.info(f"Converted {field_id}: {value} -> '{converted_values[0]}'")
                        except Exception as e:
                            logger.warning(f"Failed to convert indices for {field_id}: {e}")
        
        return converted_data

    async def _prepare_payloads(
        self,
        client: httpx.AsyncClient,
        payload: Dict[str, Any],
        mip003_schema: Optional[List[Dict[str, Any]]],
        api_key: str,
        session_cookie: Optional[str]
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Prepare payload for Kodosumi requests. When file fields are present we upload
        the bytes via the Files API and replace the original values with the JSON
        references Kodosumi expects (batchId/items structure).
        """
        if not mip003_schema:
            # Nothing to transform
            return payload, []

        field_map = {field.get('id'): field for field in mip003_schema if field.get('id')}
        prepared_payload = payload.copy()
        errors: List[str] = []

        cookies = {'kodosumi_jwt': session_cookie} if session_cookie else None
        batch_id: Optional[str] = None

        for field_id, field in field_map.items():
            if field_id not in prepared_payload:
                continue

            if field.get('type') != 'file':
                continue

            original_value = prepared_payload[field_id]
            file_url = original_value.strip() if isinstance(original_value, str) else ''
            if not file_url:
                errors.append(f"{field_id}: file URL is missing or empty")
                continue

            content, filename, _, download_error = await self._download_file(field_id, file_url)
            if download_error:
                errors.append(f"{field_id}: {download_error}")
                continue
            if content is None or filename is None:
                errors.append(f"{field_id}: failed to download file bytes")
                continue

            try:
                if batch_id is None:
                    batch_id = await self._init_file_batch(client, api_key, cookies)
                upload_id, total_chunks = await self._upload_file_via_api(
                    client,
                    batch_id,
                    filename,
                    content,
                    api_key,
                    cookies
                )
            except Exception as exc:
                errors.append(f"{field_id}: {exc}")
                continue

            prepared_payload[field_id] = json.dumps({
                "batchId": batch_id,
                "items": {
                    upload_id: {
                        "filename": filename,
                        "totalChunks": total_chunks
                    }
                }
            })

        return prepared_payload, errors

    async def _init_file_batch(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        cookies: Optional[Dict[str, str]]
    ) -> str:
        """Initialize a batch upload session and return the batch_id."""
        url = f"{self.kodosumi_server_url}/files/init_batch"
        response = await client.post(
            url,
            headers={'KODOSUMI_API_KEY': api_key},
            cookies=cookies,
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        batch_id = data.get("batch_id")
        if not batch_id:
            raise RuntimeError("files/init_batch response missing batch_id")
        logger.info(f"Initialized file batch {batch_id}")
        return batch_id

    async def _upload_file_via_api(
        self,
        client: httpx.AsyncClient,
        batch_id: str,
        filename: str,
        content: bytes,
        api_key: str,
        cookies: Optional[Dict[str, str]]
    ) -> Tuple[str, int]:
        """
        Upload a single file to Kodosumi via the Files API.
        Returns (upload_id, total_chunks).
        """
        total_chunks = max(1, math.ceil(len(content) / (1024 * 1024)))
        chunk_size = max(1, min(UPLOAD_CHUNK_SIZE, 1024 * 1024))
        total_chunks = max(1, math.ceil(len(content) / chunk_size))
        init_payload = {
            "filename": filename,
            "total_chunks": total_chunks,
            "batch_id": batch_id
        }
        init_url = f"{self.kodosumi_server_url}/files/init"
        init_resp = await client.post(
            init_url,
            headers={'KODOSUMI_API_KEY': api_key},
            cookies=cookies,
            json=init_payload,
            timeout=30.0
        )
        init_resp.raise_for_status()
        upload_id = init_resp.json().get("upload_id")
        if not upload_id:
            raise RuntimeError("files/init response missing upload_id")

        chunk_url = f"{self.kodosumi_server_url}/files/chunk"
        for chunk_number in range(total_chunks):
            start = chunk_number * chunk_size
            end = start + chunk_size
            chunk_bytes = content[start:end]
            if not chunk_bytes:
                raise RuntimeError(f"Chunk {chunk_number} read empty data")

            form_data = {
                "upload_id": upload_id,
                "chunk_number": str(chunk_number)
            }
            files = {
                "chunk": (
                    f"chunk_{chunk_number}",
                    chunk_bytes,
                    "application/octet-stream"
                )
            }
            chunk_resp = await client.post(
                chunk_url,
                headers={'KODOSUMI_API_KEY': api_key},
                cookies=cookies,
                data=form_data,
                files=files,
                timeout=30.0
            )
            if chunk_resp.status_code == 413:
                raise RuntimeError(
                    "Uploaded chunk exceeded allowed size (HTTP 413). "
                    "Try reducing KODOSUMI_FILES_CHUNK_SIZE."
                )
            chunk_resp.raise_for_status()

        logger.info(
            f"Uploaded file '{filename}' via Files API with upload_id {upload_id}"
        )
        return upload_id, total_chunks

    async def _download_file(
        self,
        field_id: str,
        file_url: str
    ) -> Tuple[Optional[bytes], Optional[str], Optional[str], Optional[str]]:
        """Download a file from the provided URL so it can be uploaded to Kodosumi."""
        logger.info(f"Downloading file for field {field_id} from {file_url}")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(file_url, timeout=60.0)
                response.raise_for_status()
                content = response.content
                headers = response.headers

            parsed = urlparse(file_url)
            filename = os.path.basename(parsed.path) or f"{field_id}.bin"
            content_type = headers.get('Content-Type') or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            return content, filename, content_type, None
        except Exception as exc:
            logger.error(f"Failed to download file for field {field_id} from {file_url}: {exc}")
            return None, None, None, str(exc)

    def _serialize_form_value(self, value: Any) -> str:
        """Serialize non-file values for form submission."""
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return ""
        return str(value)

    def _convert_date_fields(self, payload: Dict[str, Any], schema: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Convert date/datetime fields from ISO format to Kodosumi-compatible format.

        Kodosumi expects:
        - date fields: 'YYYY-MM-DD'
        - datetime fields: 'YYYY-MM-DDTHH:MM' (no seconds, no timezone)
        - time fields: 'HH:MM'

        API/MIP003 may send:
        - '2025-12-31T23:00:00.000Z' (ISO with timezone)
        - '2025-12-31T23:00:00Z'
        - '2025-12-31T23:00:00'
        - '2025-12-31'
        """
        converted = payload.copy()

        # Build a map of field types from schema
        field_types = {}
        for field in schema:
            field_id = field.get('id')
            field_type = field.get('type')
            if field_id and field_type:
                field_types[field_id] = field_type

        for field_id, value in payload.items():
            if not isinstance(value, str) or not value:
                continue

            field_type = field_types.get(field_id)

            # Check if this looks like an ISO date string
            if not self._looks_like_iso_date(value):
                continue

            try:
                if field_type == 'date':
                    # Convert to YYYY-MM-DD
                    converted[field_id] = self._parse_iso_to_date(value)
                    logger.info(f"Converted date field {field_id}: '{value}' -> '{converted[field_id]}'")

                elif field_type == 'datetime':
                    # Convert to YYYY-MM-DDTHH:MM (no seconds, no timezone)
                    converted[field_id] = self._parse_iso_to_datetime_local(value)
                    logger.info(f"Converted datetime field {field_id}: '{value}' -> '{converted[field_id]}'")

                elif field_type == 'time':
                    # Convert to HH:MM
                    converted[field_id] = self._parse_iso_to_time(value)
                    logger.info(f"Converted time field {field_id}: '{value}' -> '{converted[field_id]}'")

                elif field_type is None and self._looks_like_iso_date(value):
                    # Field type not in schema but value looks like ISO date
                    # Try to infer and convert
                    if 'T' in value:
                        converted[field_id] = self._parse_iso_to_datetime_local(value)
                        logger.info(f"Converted inferred datetime field {field_id}: '{value}' -> '{converted[field_id]}'")
                    else:
                        converted[field_id] = self._parse_iso_to_date(value)
                        logger.info(f"Converted inferred date field {field_id}: '{value}' -> '{converted[field_id]}'")

            except Exception as e:
                logger.warning(f"Failed to convert date field {field_id} with value '{value}': {e}")

        return converted

    def _looks_like_iso_date(self, value: str) -> bool:
        """Check if a string looks like an ISO date/datetime."""
        # Match patterns like:
        # 2025-12-31
        # 2025-12-31T23:00:00
        # 2025-12-31T23:00:00.000Z
        # 2025-12-31T23:00:00Z
        # 2025-12-31T23:00:00+00:00
        iso_pattern = r'^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?$'
        return bool(re.match(iso_pattern, value))

    def _parse_iso_to_date(self, value: str) -> str:
        """Parse ISO date string and return YYYY-MM-DD format."""
        # Handle various ISO formats
        value = value.replace('Z', '+00:00')

        # Try parsing with fromisoformat
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

        # Fallback: extract just the date part
        match = re.match(r'^(\d{4}-\d{2}-\d{2})', value)
        if match:
            return match.group(1)

        return value

    def _parse_iso_to_datetime_local(self, value: str) -> str:
        """Parse ISO datetime string and return YYYY-MM-DDTHH:MM format (no seconds, no TZ)."""
        value = value.replace('Z', '+00:00')

        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime('%Y-%m-%dT%H:%M')
        except ValueError:
            pass

        # Fallback: extract date and time without seconds
        match = re.match(r'^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})', value)
        if match:
            return f"{match.group(1)}T{match.group(2)}"

        return value

    def _parse_iso_to_time(self, value: str) -> str:
        """Parse ISO time/datetime string and return HH:MM format."""
        value = value.replace('Z', '+00:00')

        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime('%H:%M')
        except ValueError:
            pass

        # Fallback: extract just the time part
        match = re.search(r'T?(\d{2}:\d{2})', value)
        if match:
            return match.group(1)

        return value

    async def get_flow_schema(self, conn: asyncpg.Connection, flow_uid: str) -> Optional[List[Dict[str, Any]]]:
        """Get the MIP003 schema for a flow."""
        try:
            query = """
                SELECT mip003_schema 
                FROM flows 
                WHERE uid = $1
            """
            
            row = await conn.fetchrow(query, flow_uid)
            if row and row['mip003_schema']:
                schema = json.loads(row['mip003_schema']) if isinstance(row['mip003_schema'], str) else row['mip003_schema']
                return schema
            return None
        except Exception as e:
            logger.error(f"Failed to get schema for flow {flow_uid}: {e}")
            return None

    def _extract_error_message(self, response: httpx.Response, payload: Optional[Dict[str, Any]] = None) -> str:
        """Extract a human-readable error message from a Kodosumi response."""
        data = payload
        if data is None:
            try:
                data = response.json()
            except Exception:
                data = None

        if isinstance(data, dict):
            errors = data.get("errors")
            if isinstance(errors, dict) and errors:
                parts = []
                for field, messages in errors.items():
                    if isinstance(messages, list):
                        for message in messages:
                            parts.append(f"{field}: {message}")
                    else:
                        parts.append(f"{field}: {messages}")
                if parts:
                    return "Kodosumi error: " + "; ".join(parts)

            detail = data.get("detail")
            if detail:
                return f"Kodosumi error: {detail}"

        text = response.text.strip()
        status = response.status_code
        if text:
            preview = text if len(text) <= 500 else f"{text[:497]}..."
            return f"Kodosumi error (status {status}): {preview}"

        return f"Kodosumi error (status {status})"

    async def start_job_in_kodosumi(
        self,
        job: Dict[str, Any],
        api_key: str,
        session_cookie: Optional[str],
        conn: asyncpg.Connection
    ) -> Tuple[Optional[str], Optional[str]]:
        """Start a job in Kodosumi and return the flow identifier (fid) and optional error message."""
        try:
            # Use the flow URL from the database if available
            if job.get('flow_url'):
                # The flow URL is already in the correct format: /-/127.0.0.1/8001/youtube_analysis/-/
                kodosumi_url = f"{self.kodosumi_server_url}{job['flow_url']}"
            else:
                # Fallback to constructing URL from identifier
                flow_identifier = job.get('flow_url_identifier') or job.get('flow_summary', 'default')
                flow_identifier = flow_identifier.replace(' ', '%20')
                kodosumi_url = f"{self.kodosumi_server_url}/-/localhost/8001/{flow_identifier}/-/"
            
            logger.info(f"Starting job {job['job_id']} in Kodosumi at: {kodosumi_url}")
            
            # Prepare the request payload with the job's input data
            # Ensure payload is a dict, not a string
            payload = job['input_data']
            
            # If payload is a string, parse it as JSON
            if isinstance(payload, str):
                logger.warning(f"Input data is a string, parsing as JSON: {payload}")
                payload = json.loads(payload)
            
            # Get the flow schema and convert indices to values
            schema = await self.get_flow_schema(conn, job['flow_uid'])
            logger.info(f"Retrieved schema for flow {job['flow_uid']}: {bool(schema)}")
            if schema:
                # Log before conversion
                logger.info(f"Payload before conversion: {payload}")
                payload = await self.convert_indices_to_values(payload, schema)
                logger.info(f"Payload after indices conversion: {payload}")

                # Convert date/datetime fields from ISO format to Kodosumi format
                payload = self._convert_date_fields(payload, schema)
                logger.info(f"Payload after date conversion: {payload}")

                # Add empty strings for missing optional fields to handle Kodosumi's .strip() calls
                for field in schema:
                    field_id = field.get('id')
                    if field_id and field_id not in payload:
                        # Check if field is optional
                        validations = field.get('validations', [])
                        is_optional = any(
                            v.get('validation') == 'optional' and v.get('value') == 'true' 
                            for v in validations
                        )
                        if is_optional:
                            # Add empty string for optional string/textarea fields
                            if field.get('type') in ['string', 'textarea']:
                                payload[field_id] = ""
                                logger.info(f"Added empty string for optional field: {field_id}")
            else:
                logger.warning(f"No schema found for flow {job['flow_uid']}, using payload as-is")
            
            # Special handling for model_family field - Kodosumi expects a string, not array
            if 'model_family' in payload and isinstance(payload['model_family'], list):
                if payload['model_family']:
                    payload['model_family'] = payload['model_family'][0]
                    logger.info(f"Converted model_family from list to string: {payload['model_family']}")
                else:
                    payload['model_family'] = ""
                    logger.info("Converted empty model_family list to empty string")
            
            # Make the request to Kodosumi using the API key
            cookies = {'kodosumi_jwt': session_cookie} if session_cookie else None

            async with httpx.AsyncClient() as client:
                headers = {
                    'KODOSUMI_API_KEY': api_key
                }

                prepared_payload, file_errors = await self._prepare_payloads(
                    client,
                    payload,
                    schema,
                    api_key,
                    session_cookie
                )
                if file_errors:
                    error_message = "; ".join(file_errors)
                    logger.error(f"File preparation errors for job {job['job_id']}: {error_message}")
                    return None, error_message

                headers['Content-Type'] = 'application/json'
                if session_cookie:
                    logger.info("Including session cookie for Kodosumi request")
                logger.info(f"Prepared payload: {prepared_payload}")

                response = await client.post(
                    kodosumi_url,
                    headers=headers,
                    cookies=cookies,
                    timeout=30.0,
                    json=prepared_payload
                )
                
                if response.status_code == 200:
                    result = response.json()
                    fid = result.get("result")
                    
                    if fid:
                        logger.info(f"Successfully started job {job['job_id']} in Kodosumi with fid: {fid}")
                        return fid, None
                    else:
                        error_message = self._extract_error_message(response, result)
                        logger.warning(
                            f"Kodosumi response for job {job['job_id']} did not contain 'result' field."
                            f" Error: {error_message}"
                        )
                        return None, error_message
                else:
                    error_message = self._extract_error_message(response)
                    logger.error(
                        f"Failed to start job {job['job_id']} in Kodosumi. "
                        f"Status: {response.status_code}, Error: {error_message}"
                    )
                    return None, error_message
                    
        except Exception as e:
            logger.error(f"Error starting job {job['job_id']} in Kodosumi: {e}")
            return None, str(e)
    
    async def update_job_with_fid(self, conn: asyncpg.Connection, job_id: str, fid: str) -> bool:
        """Update job with Kodosumi flow identifier and set waiting_for_start_in_kodosumi to false."""
        try:
            # Add kodosumi_fid column to result JSON or create new field
            query = """
                UPDATE jobs 
                SET waiting_for_start_in_kodosumi = false,
                    result = COALESCE(result, '{}'::jsonb) || jsonb_build_object('kodosumi_fid', $2::text),
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $1::uuid
            """
            
            result = await conn.execute(query, job_id, fid)
            
            if result == "UPDATE 1":
                logger.info(f"Successfully updated job {job_id} with Kodosumi fid: {fid}")
                return True
            else:
                logger.warning(f"No rows updated for job {job_id}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to update job {job_id} with fid {fid}: {e}")
            return False

    async def update_job_message(self, conn: asyncpg.Connection, job_id: str, message: str) -> bool:
        """Update the job message for visibility in status endpoints and UI."""
        try:
            query = """
                UPDATE jobs
                SET message = $2,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $1::uuid
            """
            result = await conn.execute(query, job_id, message)
            if result == "UPDATE 1":
                logger.info(f"Updated job {job_id} message to: {message}")
                return True
            logger.warning(f"No rows updated when setting message for job {job_id}")
            return False
        except Exception as e:
            logger.error(f"Failed to update message for job {job_id}: {e}")
            return False
    
    async def increment_retry_count(self, conn: asyncpg.Connection, job_id: str) -> bool:
        """Increment the kodosumi_start_attempts counter for a job."""
        try:
            query = """
                UPDATE jobs 
                SET kodosumi_start_attempts = kodosumi_start_attempts + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $1::uuid
            """
            
            result = await conn.execute(query, job_id)
            
            if result == "UPDATE 1":
                logger.info(f"Incremented retry count for job {job_id}")
                return True
            else:
                logger.warning(f"No rows updated when incrementing retry count for job {job_id}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to increment retry count for job {job_id}: {e}")
            return False
    
    async def mark_job_as_error(self, conn: asyncpg.Connection, job_id: str, error_message: str) -> bool:
        """Mark a job as error after max retries."""
        try:
            query = """
                UPDATE jobs 
                SET status = 'error',
                    waiting_for_start_in_kodosumi = false,
                    message = $2,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $1::uuid
            """
            
            result = await conn.execute(query, job_id, error_message)
            
            if result == "UPDATE 1":
                logger.warning(f"Marked job {job_id} as error after max retries")
                return True
            else:
                logger.warning(f"No rows updated when marking job {job_id} as error")
                return False
                
        except Exception as e:
            logger.error(f"Failed to mark job {job_id} as error: {e}")
            return False
    
    async def process_jobs(self):
        """Main process to start jobs in Kodosumi."""
        # Initialize cron logger
        cron_logger = CronExecutionLogger('kodosumi-starter', self.database_url)
        await cron_logger.log_start()
        
        items_processed = 0
        error = None
        
        try:
            # Connect to database
            conn = await asyncpg.connect(self.database_url)
            
            # Get latest API credentials
            api_key, session_cookie = await self.get_latest_credentials(conn)
            if not api_key:
                logger.warning("No API key found in database. Cannot start jobs in Kodosumi.")
                await conn.close()
                return
            
            logger.info(f"Using API key: {api_key[:20]}...")
            if session_cookie:
                logger.info("Session cookie available for job start requests")
            else:
                logger.warning("No session cookie available; Kodosumi may reject requests")
            
            # Get jobs waiting to be started in Kodosumi
            waiting_jobs = await self.get_jobs_waiting_for_kodosumi(conn)
            
            if not waiting_jobs:
                logger.info("No jobs waiting to be started in Kodosumi")
                await conn.close()
                return
            
            logger.info(f"Found {len(waiting_jobs)} jobs waiting to be started in Kodosumi")
            
            # Also check for jobs that have exceeded max attempts
            exceeded_query = """
                SELECT job_id FROM jobs 
                WHERE status = 'running' 
                  AND waiting_for_start_in_kodosumi = true
                  AND kodosumi_start_attempts >= $1
            """
            exceeded_rows = await conn.fetch(exceeded_query, MAX_START_ATTEMPTS)
            for row in exceeded_rows:
                job_id = str(row['job_id'])
                error_msg = f"Failed to start job in Kodosumi after {MAX_START_ATTEMPTS} attempts"
                await self.mark_job_as_error(conn, job_id, error_msg)
                logger.warning(f"Marked job {job_id} as error (exceeded max attempts)")
            
            # Process each job
            for job in waiting_jobs:
                job_id = str(job['job_id'])
                attempts = job['kodosumi_start_attempts']
                
                logger.info(
                    f"Processing job {job_id} for flow: {job['flow_summary']} "
                    f"(attempt {attempts + 1}/{MAX_START_ATTEMPTS})"
                )
                
                # Start job in Kodosumi
                fid, error_message = await self.start_job_in_kodosumi(job, api_key, session_cookie, conn)
                
                if fid:
                    # Update job with the Kodosumi flow identifier
                    success = await self.update_job_with_fid(conn, job_id, fid)
                    
                    if success:
                        logger.info(f"Successfully processed job {job_id}")
                        items_processed += 1  # Count successful starts
                    else:
                        logger.error(f"Failed to update job {job_id} in database")
                else:
                    # Failed to start in Kodosumi
                    logger.error(
                        f"Failed to start job {job_id} in Kodosumi "
                        f"(attempt {attempts + 1}/{MAX_START_ATTEMPTS})"
                    )

                    # Persist the latest error message so it is visible via status/API
                    if error_message:
                        await self.update_job_message(conn, job_id, error_message)
                    
                    # Increment retry count
                    await self.increment_retry_count(conn, job_id)
                    
                    # Check if this was the 5th attempt
                    if attempts + 1 >= MAX_START_ATTEMPTS:
                        detailed_error = (
                            f"Failed to start job in Kodosumi after "
                            f"{MAX_START_ATTEMPTS} attempts"
                        )
                        if error_message:
                            detailed_error = f"{detailed_error}. Last error: {error_message}"
                        await self.mark_job_as_error(conn, job_id, detailed_error)
                        logger.error(
                            f"Job {job_id} marked as error after "
                            f"{MAX_START_ATTEMPTS} failed attempts: {detailed_error}"
                        )
            
            await conn.close()
            logger.info("Kodosumi job starting completed successfully")
            
        except Exception as e:
            error = str(e)
            logger.error(f"Error in Kodosumi job processing: {e}")
            raise
            
        finally:
            await cron_logger.log_completion(items_processed, error)

async def main():
    """Main entry point for the Kodosumi starter."""
    logger.info("Starting Kodosumi job starter...")
    
    starter = KodosumiStarter()
    await starter.process_jobs()
    
    logger.info("Kodosumi job starter completed")

if __name__ == "__main__":
    asyncio.run(main())
