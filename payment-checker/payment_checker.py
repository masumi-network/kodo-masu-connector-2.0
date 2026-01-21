#!/usr/bin/env python3
import asyncio
import asyncpg
import aiohttp
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

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

# Payment timeout: fail jobs that have been awaiting payment for too long
PAYMENT_TIMEOUT_HOURS = int(os.getenv('PAYMENT_TIMEOUT_HOURS', '12'))

# Max concurrent API requests when resolving payments
MAX_CONCURRENT_REQUESTS = int(os.getenv('PAYMENT_CHECKER_CONCURRENCY', '10'))


class PaymentChecker:
    """Service for checking payment status and updating job statuses."""

    def __init__(self):
        # Database configuration
        self.database_url = self._build_database_url()

        # Payment service configuration
        self.payment_service_url = os.getenv('PAYMENT_SERVICE_URL')
        self.payment_api_key = os.getenv('PAYMENT_API_KEY')
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

    async def resolve_payment_by_blockchain_id(
        self,
        session: aiohttp.ClientSession,
        blockchain_id: str
    ) -> Optional[Dict[str, Any]]:
        """Resolve a payment by its blockchain identifier using the dedicated API endpoint.

        This is the most efficient way to check a specific payment's status.
        Returns the payment data if found, None otherwise.
        """
        url = f"{self.payment_service_url}/payment/resolve-blockchain-identifier"
        headers = {
            'token': self.payment_api_key,
            'Content-Type': 'application/json'
        }
        body = {
            'blockchainIdentifier': blockchain_id,
            'network': self.network,
            'includeHistory': 'false'
        }

        try:
            async with session.post(url, headers=headers, json=body, timeout=30) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('data')
                elif response.status == 404:
                    # Payment not found - this is normal for new payments
                    return None
                else:
                    error_text = await response.text()
                    logger.error(f"Payment API returned {response.status} for {blockchain_id[:20]}...: {error_text}")
                    return None

        except asyncio.TimeoutError:
            logger.error(f"Timeout resolving payment {blockchain_id[:20]}...")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"Network error resolving payment {blockchain_id[:20]}...: {e}")
            return None
        except Exception as e:
            logger.error(f"Error resolving payment {blockchain_id[:20]}...: {e}")
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

    async def timeout_old_awaiting_payment_jobs(self, conn: asyncpg.Connection) -> int:
        """Mark jobs that have been awaiting payment for too long as failed.

        Returns the number of jobs timed out.
        """
        # Use naive datetime to match database timestamps (which are stored without timezone)
        timeout_cutoff = datetime.utcnow() - timedelta(hours=PAYMENT_TIMEOUT_HOURS)

        query = """
            UPDATE jobs
            SET status = 'failed',
                message = $1,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'awaiting_payment'
              AND created_at < $2
            RETURNING job_id
        """

        timeout_message = f"Payment not received within {PAYMENT_TIMEOUT_HOURS} hours. Job timed out."

        try:
            rows = await conn.fetch(query, timeout_message, timeout_cutoff)
            timed_out_count = len(rows)

            if timed_out_count > 0:
                logger.info(f"Timed out {timed_out_count} jobs that were awaiting payment for more than {PAYMENT_TIMEOUT_HOURS} hours")
                for row in rows[:10]:  # Log first 10
                    logger.info(f"  Timed out job: {row['job_id']}")
                if timed_out_count > 10:
                    logger.info(f"  ... and {timed_out_count - 10} more")

            return timed_out_count

        except Exception as e:
            logger.error(f"Error timing out old jobs: {e}")
            return 0

    async def check_single_job_payment(
        self,
        session: aiohttp.ClientSession,
        job: Dict[str, Any],
        semaphore: asyncio.Semaphore
    ) -> Optional[Dict[str, Any]]:
        """Check payment status for a single job using the resolve API.

        Returns a dict with job_id and payment data if FundsLocked, None otherwise.
        """
        blockchain_id = self.extract_blockchain_identifier(job['payment_data'])
        if not blockchain_id:
            logger.warning(f"Job {job['job_id']} has no blockchain identifier")
            return None

        async with semaphore:
            payment = await self.resolve_payment_by_blockchain_id(session, blockchain_id)

        if not payment:
            return None

        on_chain_state = payment.get('onChainState')

        if on_chain_state == 'FundsLocked':
            logger.info(f"Job {job['job_id']} payment state: FundsLocked")
            return {'job': job, 'payment': payment}
        else:
            logger.debug(f"Job {job['job_id']} payment state: {on_chain_state}")
            return None

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

            # First, timeout any jobs that have been awaiting payment for too long
            timed_out = await self.timeout_old_awaiting_payment_jobs(conn)
            items_processed += timed_out

            # Get all jobs awaiting payment
            awaiting_jobs = await self.get_awaiting_payment_jobs(conn)

            if not awaiting_jobs:
                logger.info("No jobs awaiting payment found")
                await conn.close()
                return

            logger.info(f"Found {len(awaiting_jobs)} jobs awaiting payment")

            # Check payments concurrently with rate limiting
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

            async with aiohttp.ClientSession() as session:
                tasks = [
                    self.check_single_job_payment(session, job, semaphore)
                    for job in awaiting_jobs
                ]

                results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results and update jobs
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Error checking payment: {result}")
                    continue

                if result is None:
                    continue

                job = result['job']
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
                    items_processed += 1
                else:
                    logger.error(f"Failed to update job {job['job_id']} status")

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
