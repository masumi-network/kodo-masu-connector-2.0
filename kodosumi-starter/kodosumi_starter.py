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
              AND j.kodosumi_start_attempts < 5
            ORDER BY j.created_at ASC
        """
        
        rows = await conn.fetch(query)
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
    
    async def start_job_in_kodosumi(self, job: Dict[str, Any], api_key: str) -> Optional[str]:
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
            
            logger.info(f"Sending payload: {payload}")
            
            # Make the request to Kodosumi using the API key
            async with httpx.AsyncClient() as client:
                headers = {
                    'KODOSUMI_API_KEY': api_key,
                    'Content-Type': 'application/json'
                }
                
                # Log the full request details for debugging
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
                  AND kodosumi_start_attempts >= 5
            """
            exceeded_rows = await conn.fetch(exceeded_query)
            for row in exceeded_rows:
                job_id = str(row['job_id'])
                error_msg = f"Failed to start job in Kodosumi after 5 attempts"
                await self.mark_job_as_error(conn, job_id, error_msg)
                logger.warning(f"Marked job {job_id} as error (exceeded max attempts)")
            
            # Process each job
            for job in waiting_jobs:
                job_id = str(job['job_id'])
                attempts = job['kodosumi_start_attempts']
                
                logger.info(f"Processing job {job_id} for flow: {job['flow_summary']} (attempt {attempts + 1}/5)")
                
                # Start job in Kodosumi
                fid = await self.start_job_in_kodosumi(job, api_key)
                
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
                    logger.error(f"Failed to start job {job_id} in Kodosumi (attempt {attempts + 1}/5)")
                    
                    # Increment retry count
                    await self.increment_retry_count(conn, job_id)
                    
                    # Check if this was the 5th attempt
                    if attempts + 1 >= 5:
                        error_msg = f"Failed to start job in Kodosumi after 5 attempts"
                        await self.mark_job_as_error(conn, job_id, error_msg)
                        logger.error(f"Job {job_id} marked as error after 5 failed attempts")
            
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