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
from typing import List, Dict, Any, Optional
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
        self.kodosumi_server_url = os.getenv('KODOSUMI_SERVER_URL', 'http://localhost:3370')
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
    
    async def get_latest_api_key(self, conn: asyncpg.Connection) -> Optional[str]:
        """Get the latest API key from the database."""
        try:
            query = """
                SELECT api_key FROM api_keys 
                ORDER BY created_at DESC 
                LIMIT 1
            """
            row = await conn.fetchrow(query)
            if row:
                return row['api_key']
            return None
        except Exception as e:
            logger.error(f"Failed to get latest API key: {e}")
            return None
    
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

    async def start_job_in_kodosumi(self, job: Dict[str, Any], api_key: str, conn: asyncpg.Connection) -> Optional[str]:
        """Start a job in Kodosumi and return the flow identifier (fid)."""
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
                logger.info(f"Payload after conversion: {payload}")
                
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
            
            logger.info(f"Sending payload: {payload}")
            
            # Make the request to Kodosumi using the API key
            async with httpx.AsyncClient() as client:
                headers = {
                    'KODOSUMI_API_KEY': api_key,
                    'Content-Type': 'application/json'
                }
                
                # Log the full request details for debugging
                # Last-resort fix for model_family field
                logger.info(f"Checking model_family before fix: {payload.get('model_family')} (type: {type(payload.get('model_family'))})")
                if 'model_family' in payload and isinstance(payload['model_family'], list):
                    logger.info(f"model_family is a list with {len(payload['model_family'])} items")
                    if payload['model_family']:
                        # If it's still a list of strings, take the first one
                        payload['model_family'] = str(payload['model_family'][0])
                        logger.warning(f"Last-resort conversion: model_family converted to string: {payload['model_family']}")
                
                logger.info(f"Request headers: {headers}")
                logger.info(f"Request payload type: {type(payload)}")
                logger.info(f"Request payload: {json.dumps(payload)}")
                
                response = await client.post(
                    kodosumi_url,
                    headers=headers,
                    json=payload,
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    fid = result.get("result")
                    
                    if fid:
                        logger.info(f"Successfully started job {job['job_id']} in Kodosumi with fid: {fid}")
                        return fid
                    else:
                        logger.warning(f"Kodosumi response for job {job['job_id']} did not contain 'result' field: {result}")
                        return None
                else:
                    logger.error(f"Failed to start job {job['job_id']} in Kodosumi. Status: {response.status_code}, Response: {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error starting job {job['job_id']} in Kodosumi: {e}")
            return None
    
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
            
            # Get latest API key
            api_key = await self.get_latest_api_key(conn)
            if not api_key:
                logger.warning("No API key found in database. Cannot start jobs in Kodosumi.")
                await conn.close()
                return
            
            logger.info(f"Using API key: {api_key[:20]}...")
            
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
                fid = await self.start_job_in_kodosumi(job, api_key, conn)
                
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
                    
                    # Increment retry count
                    await self.increment_retry_count(conn, job_id)
                    
                    # Check if this was the 5th attempt
                    if attempts + 1 >= MAX_START_ATTEMPTS:
                        error_msg = (
                            f"Failed to start job in Kodosumi after "
                            f"{MAX_START_ATTEMPTS} attempts"
                        )
                        await self.mark_job_as_error(conn, job_id, error_msg)
                        logger.error(
                            f"Job {job_id} marked as error after "
                            f"{MAX_START_ATTEMPTS} failed attempts"
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
