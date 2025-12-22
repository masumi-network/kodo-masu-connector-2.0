#!/usr/bin/env python3
import asyncio
import asyncpg
import logging
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from masumi.payment import Payment
from masumi.config import Config as MasumiConfig

# Ensure shared modules are importable both locally and in containers
CURRENT_DIR = Path(__file__).resolve().parent
for base_dir in (CURRENT_DIR, CURRENT_DIR.parent):
    shared_dir = base_dir / 'shared'
    if shared_dir.exists():
        base_path = str(base_dir)
        if base_path not in sys.path:
            sys.path.append(base_path)
        break

from shared.cron_logger import CronExecutionLogger
from shared.status_messages import WORKING_STATUS_MESSAGE

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

RUNNING_MESSAGE = "The agent is now working on your task. Please check back soon."

class PaymentChecker:
    """Service for checking payment status and updating job statuses."""
    
    def __init__(self):
        # Database configuration
        self.database_url = self._build_database_url()
        
        # Masumi configuration
        self.masumi_config = MasumiConfig(
            payment_service_url=os.getenv('PAYMENT_SERVICE_URL'),
            payment_api_key=os.getenv('PAYMENT_API_KEY')
        )
        
        self.network = os.getenv('NETWORK', 'mainnet')
        
    def _build_database_url(self) -> str:
        """Build PostgreSQL connection URL from environment variables."""
        host = os.getenv('POSTGRES_HOST', 'postgres')
        port = os.getenv('POSTGRES_PORT', '5432')
        database = os.getenv('POSTGRES_DB')
        user = os.getenv('POSTGRES_USER')
        password = os.getenv('POSTGRES_PASSWORD')
        
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"
    
    async def get_awaiting_payment_jobs(self, conn: asyncpg.Connection) -> List[Dict[str, Any]]:
        """Get all jobs that are awaiting payment."""
        query = """
            SELECT j.job_id, j.flow_uid, j.identifier_from_purchaser, j.input_data, 
                   j.payment_data, j.created_at, j.updated_at,
                   j.agent_identifier_used, j.payment_required,
                   COALESCE(f.agent_identifier_default, f.agent_identifier) AS flow_agent_identifier
            FROM jobs j
            LEFT JOIN flows f ON j.flow_uid = f.uid
            WHERE j.status = 'awaiting_payment' AND COALESCE(j.payment_required, TRUE) = TRUE
            ORDER BY j.created_at ASC
        """

        rows = await conn.fetch(query)
        jobs = []

        for row in rows:
            # Parse JSON fields
            import json
            input_data = json.loads(row['input_data']) if row['input_data'] else {}
            payment_data = json.loads(row['payment_data']) if row['payment_data'] else {}

            jobs.append({
                'job_id': row['job_id'],
                'flow_uid': row['flow_uid'],
                'identifier_from_purchaser': row['identifier_from_purchaser'],
                'input_data': input_data,
                'payment_data': payment_data,
                'agent_identifier': row['agent_identifier_used'] or row['flow_agent_identifier'],
                'payment_required': row['payment_required'] if row['payment_required'] is not None else True,
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            })

        return jobs
    
    async def get_payment_status_for_job(self, agent_identifier: str, job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get payment status for a specific job from Masumi service."""
        try:
            # Create a Payment instance for this specific job
            payment = Payment(
                agent_identifier=agent_identifier,
                config=self.masumi_config,
                network=self.network,
                identifier_from_purchaser=job['identifier_from_purchaser'],
                input_data=job['input_data']
            )
            
            # Add the specific blockchain identifier for this job
            blockchain_id = self.extract_blockchain_identifier(job['payment_data'])
            if blockchain_id:
                payment.payment_ids.add(blockchain_id)
                logger.info(f"Added payment ID {blockchain_id[:20]}... for job {job['job_id']}")
            else:
                logger.warning(f"Job {job['job_id']} has no blockchain identifier")
                return None
            
            logger.info(f"Checking payment status for job {job['job_id']} with agent {agent_identifier[:20]}...")
            
            # Check payment status - use default limit (100 is max allowed)
            response = await payment.check_payment_status()
            
            logger.info(f"Retrieved payment status for job {job['job_id']}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to get payment status for job {job['job_id']}: {e}")
            return None
    
    async def update_job_status(
        self,
        conn: asyncpg.Connection,
        job_id: str,
        new_status: str,
        *,
        waiting_for_start: bool = True,
        message: Optional[str] = None,
    ) -> bool:
        """Update job status and waiting_for_start_in_kodosumi flag."""
        try:
            query = """
                UPDATE jobs 
                SET status = $1,
                    waiting_for_start_in_kodosumi = $2,
                    message = COALESCE($4, message),
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = $3
            """
            
            result = await conn.execute(query, new_status, waiting_for_start, job_id, message)
            return True
            
        except Exception as e:
            logger.error(f"Failed to update job {job_id}: {e}")
            return False
    
    def extract_blockchain_identifier(self, payment_data: Dict[str, Any]) -> Optional[str]:
        """Extract blockchain identifier from payment data."""
        # Check nested structure first (new format)
        if 'data' in payment_data and isinstance(payment_data['data'], dict):
            return payment_data['data'].get('blockchainIdentifier')
        
        # Fallback to direct access (old format)
        return payment_data.get('blockchainIdentifier')
    
    async def process_payments(self):
        """Main process to check payments and update job statuses."""
        # Initialize cron logger
        cron_logger = CronExecutionLogger('payment-checker', self.database_url)
        await cron_logger.log_start()
        
        items_processed = 0
        error = None
        
        try:
            # Connect to database
            conn = await asyncpg.connect(self.database_url)
            
            # Get all jobs awaiting payment
            awaiting_jobs = await self.get_awaiting_payment_jobs(conn)
            
            if not awaiting_jobs:
                logger.info("No jobs awaiting payment found")
                await conn.close()
                return
            
            logger.info(f"Found {len(awaiting_jobs)} jobs awaiting payment")
            
            # Process each job individually for better scalability
            for job in awaiting_jobs:
                agent_id = job['agent_identifier']
                if not agent_id:
                    logger.warning(f"Job {job['job_id']} has no agent identifier")
                    continue
                
                job_blockchain_id = self.extract_blockchain_identifier(job['payment_data'])
                if not job_blockchain_id:
                    logger.warning(f"Job {job['job_id']} has no blockchain identifier")
                    continue
                
                logger.info(f"Checking payment for job {job['job_id']} with agent: {agent_id[:20]}...")
                
                # Create individual payment instance for this job
                payment_status_response = await self.get_payment_status_for_job(agent_id, job)
                
                if not payment_status_response or payment_status_response.get('status') != 'success':
                    logger.warning(f"Failed to get payment status for job {job['job_id']}")
                    continue
                
                # Extract payments data
                payments_data = payment_status_response.get('data', {})
                all_payments = payments_data.get('Payments', [])
                
                if not all_payments:
                    logger.info(f"No payments found for job {job['job_id']}")
                    continue
                
                logger.info(f"Found {len(all_payments)} payments for job {job['job_id']}")
                
                # Find matching payment by blockchain identifier
                matching_payment = None
                for payment in all_payments:
                    if payment.get('blockchainIdentifier') == job_blockchain_id:
                        matching_payment = payment
                        break
                
                if not matching_payment:
                    logger.info(f"No matching payment found for job {job['job_id']} with blockchain ID {job_blockchain_id[:20]}...")
                    continue
                
                # Check if payment is locked (funds are available)
                on_chain_state = matching_payment.get('onChainState')
                
                if on_chain_state == 'FundsLocked':
                    logger.info(f"Payment for job {job['job_id']} is now locked. Updating status to 'running'")
                    
                    success = await self.update_job_status(
                        conn,
                        str(job['job_id']),
                        'running',
                        waiting_for_start=True,
                        message=RUNNING_MESSAGE
                    )
                    
                    if success:
                        logger.info(f"Successfully updated job {job['job_id']} to 'running' status")
                        items_processed += 1  # Count successful updates
                    else:
                        logger.error(f"Failed to update job {job['job_id']} status")
                else:
                    logger.info(f"Job {job['job_id']} payment state is '{on_chain_state}', keeping as awaiting_payment")
            
            await conn.close()
            logger.info("Payment checking completed successfully")
            
        except Exception as e:
            error = str(e)
            logger.error(f"Error in payment processing: {e}")
            raise
            
        finally:
            await cron_logger.log_completion(items_processed, error)

async def main():
    """Main entry point for the payment checker."""
    logger.info("Starting payment checker...")
    
    checker = PaymentChecker()
    await checker.process_payments()
    
    logger.info("Payment checker completed")

if __name__ == "__main__":
    asyncio.run(main())
