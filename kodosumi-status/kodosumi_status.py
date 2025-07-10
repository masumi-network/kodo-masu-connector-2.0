#!/usr/bin/env python3
"""
Kodosumi Job Status Checker Service

This microservice runs every 2 minutes and:
1. Checks for jobs that are running in Kodosumi (have a kodosumi_fid)
2. Queries Kodosumi for their status
3. Updates the job when it's completed with the final result
"""
import asyncio
import asyncpg
import httpx
import logging
import os
import json
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
                   j.created_at, j.updated_at,
                   f.summary as flow_summary
            FROM jobs j
            LEFT JOIN flows f ON j.flow_uid = f.uid
            WHERE j.status = 'running' 
              AND j.result->>'kodosumi_fid' IS NOT NULL
            ORDER BY j.created_at ASC
        """
        
        rows = await conn.fetch(query)
        jobs = []
        
        for row in rows:
            jobs.append({
                'job_id': row['job_id'],
                'flow_uid': row['flow_uid'],
                'status': row['status'],
                'kodosumi_fid': row['kodosumi_fid'],
                'flow_summary': row['flow_summary'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            })
        
        return jobs
    
    async def check_job_status(self, fid: str, api_key: str) -> Optional[Dict[str, Any]]:
        """Check the status of a job in Kodosumi."""
        try:
            # Construct the status URL with extended=true for better reliability
            status_url = f"{self.kodosumi_server_url}/outputs/status/{fid}?extended=true"
            
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
                else:
                    logger.error(f"Failed to get status for FID {fid}. Status: {response.status_code}, Response: {response.text}")
                    return None
                    
        except httpx.ConnectError:
            logger.warning(f"Cannot connect to Kodosumi server for FID {fid}")
            return None
        except httpx.TimeoutException:
            logger.warning(f"Timeout while checking status for FID {fid}")
            return None
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
    
    async def process_jobs(self):
        """Main process to check status of jobs in Kodosumi."""
        try:
            # Connect to database
            conn = await asyncpg.connect(self.database_url)
            
            # Get latest API key
            api_key = await self.get_latest_api_key(conn)
            if not api_key:
                logger.warning("No API key found in database. Cannot check job status.")
                await conn.close()
                return
            
            logger.info(f"Using API key: {api_key[:20]}...")
            
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
                
                logger.info(f"Checking status for job {job_id} (FID: {fid})")
                
                # Check job status in Kodosumi
                status_result = await self.check_job_status(fid, api_key)
                
                if status_result:
                    status = status_result.get('status', 'unknown')
                    
                    if status == 'finished':
                        # Job is completed, update with final result
                        final_result = status_result.get('final')
                        if final_result:
                            logger.info(f"Job {job_id} completed in Kodosumi")
                            success = await self.update_completed_job(conn, job_id, final_result)
                            if success:
                                logger.info(f"Successfully updated completed job {job_id}")
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