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
import sys
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from database_payment_service import DatabasePaymentService

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
    
    async def get_running_jobs_with_fid(self, conn: asyncpg.Connection) -> List[Dict[str, Any]]:
        """Get all jobs that are running and have a Kodosumi FID."""
        query = """
            SELECT j.job_id, j.flow_uid, j.status, 
                   j.result->>'kodosumi_fid' as kodosumi_fid,
                   j.payment_data, j.input_data, j.identifier_from_purchaser,
                   j.agent_identifier_used, j.payment_required,
                   j.created_at, j.updated_at, j.timeout_attempts, j.not_found_attempts,
                   f.summary as flow_summary,
                   COALESCE(f.agent_identifier_default, f.agent_identifier) AS flow_agent_identifier
            FROM jobs j
            LEFT JOIN flows f ON j.flow_uid = f.uid
            WHERE j.status = 'running' 
              AND j.result->>'kodosumi_fid' IS NOT NULL
              AND j.timeout_attempts < 3
              AND j.not_found_attempts < 3
            ORDER BY j.created_at ASC
        """
        
        rows = await conn.fetch(query)
        jobs = []
        
        for row in rows:
            # Parse JSON fields
            payment_data = json.loads(row['payment_data']) if row['payment_data'] else {}
            input_data = json.loads(row['input_data']) if row['input_data'] else {}
            
            jobs.append({
                'job_id': row['job_id'],
                'flow_uid': row['flow_uid'],
                'status': row['status'],
                'kodosumi_fid': row['kodosumi_fid'],
                'flow_summary': row['flow_summary'],
                'payment_data': payment_data,
                'input_data': input_data,
                'identifier_from_purchaser': row['identifier_from_purchaser'],
                'agent_identifier': row['agent_identifier_used'] or row['flow_agent_identifier'],
                'payment_required': row['payment_required'] if row['payment_required'] is not None else True,
                'created_at': row['created_at'],
                'updated_at': row['updated_at'],
                'timeout_attempts': row['timeout_attempts'],
                'not_found_attempts': row['not_found_attempts']
            })
        
        return jobs
    
    async def check_job_status(self, fid: str, api_key: str) -> Optional[Dict[str, Any]]:
        """Check the status of a job in Kodosumi."""
        try:
            # Construct the status URL (removed extended=true as it causes timeouts)
            status_url = f"{self.kodosumi_server_url}/outputs/status/{fid}"
            
            logger.info(f"Checking status for FID {fid} at: {status_url}")
            
            # Make the request to Kodosumi using the API key
            async with httpx.AsyncClient() as client:
                headers = {
                    'KODOSUMI_API_KEY': api_key,
                    'Accept': 'application/json'
                }
                
                response = await client.get(
                    status_url,
                    headers=headers,
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
            # Parse the final result to store it properly
            try:
                final_data = json.loads(final_result)
            except json.JSONDecodeError:
                # If it's not valid JSON, store it as a string in a wrapper object
                final_data = {"raw_result": final_result}
            
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
            kodosumi_api_key = await self.get_latest_api_key(conn)
            if not kodosumi_api_key:
                logger.warning("No API key found in database. Cannot check job status.")
                await conn.close()
                return
            
            logger.info(f"Using Kodosumi API key: {kodosumi_api_key[:20]}...")
            
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
                jobs_processed += 1  # Count each job checked
                job_id = str(job['job_id'])
                fid = job['kodosumi_fid']
                
                logger.info(f"Checking status for job {job_id} (FID: {fid})")
                
                # Check job status in Kodosumi
                status_result = await self.check_job_status(fid, kodosumi_api_key)
                
                if status_result:
                    # Check if this was a timeout
                    if status_result.get('timeout'):
                        logger.warning(f"Job {job_id} timed out - incrementing timeout attempts")
                        await self.increment_timeout_attempts(conn, job_id)
                        timeout_attempts = job.get('timeout_attempts', 0) + 1
                        if timeout_attempts >= 3:
                            logger.error(f"Job {job_id} has timed out {timeout_attempts} times - marking as failed")
                            # Mark job as failed after 3 timeouts
                            await conn.execute(
                                "UPDATE jobs SET status = 'failed', message = $1, updated_at = CURRENT_TIMESTAMP WHERE job_id = $2::uuid",
                                f"Job timed out {timeout_attempts} times - likely never started in Kodosumi",
                                job_id
                            )
                            jobs_processed += 1
                        continue
                    
                    # Check if this was an error response
                    if status_result.get('error'):
                        error_type = status_result.get('error')
                        status_code = status_result.get('status_code')
                        
                        if error_type == 'not_found':
                            # Job not found in Kodosumi - increment attempts and retry
                            logger.warning(f"Job {job_id} not found in Kodosumi (404) - incrementing not_found_attempts")
                            await self.increment_not_found_attempts(conn, job_id)
                            not_found_attempts = job.get('not_found_attempts', 0) + 1
                            if not_found_attempts >= 3:
                                logger.error(f"Job {job_id} not found after {not_found_attempts} attempts - marking as failed")
                                # Mark job as failed after 3 404 attempts
                                await conn.execute(
                                    "UPDATE jobs SET status = 'failed', message = $1, updated_at = CURRENT_TIMESTAMP WHERE job_id = $2::uuid",
                                    f"Job not found in Kodosumi after {not_found_attempts} attempts (HTTP 404)",
                                    job_id
                                )
                                jobs_processed += 1
                            else:
                                logger.info(f"Job {job_id} not found (attempt {not_found_attempts}/3), will retry later")
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
                        logger.info(f"Job {job_id} is still running in Kodosumi")
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
