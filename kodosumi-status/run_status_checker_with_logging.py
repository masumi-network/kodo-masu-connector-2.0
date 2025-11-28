#!/usr/bin/env python3
"""
Example of how to update the Kodosumi status checker to include proper execution logging.
This shows how each cron service should be updated to track executions.
"""
import asyncio
import sys
import os
import fcntl
from datetime import datetime

# Add the parent directory to the Python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.append(CURRENT_DIR)
for shared_candidate in (
    os.path.join(CURRENT_DIR, 'shared'),
    os.path.join(CURRENT_DIR, '..', 'shared')
):
    if os.path.isdir(shared_candidate) and shared_candidate not in sys.path:
        sys.path.append(shared_candidate)

from kodosumi_status import KodosumiStatusChecker
from cron_logger import CronExecutionLogger
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def run_once():
    """Run the Kodosumi status checker once with execution logging."""
    checker = KodosumiStatusChecker()
    
    # Initialize cron execution logger
    cron_logger = CronExecutionLogger('kodosumi-status', checker.database_url)
    
    # Log execution start
    await cron_logger.log_start()
    
    items_processed = 0
    error = None
    
    try:
        logger.info("=== Starting Kodosumi status checking ===")
        start_time = datetime.utcnow()
        
        # The actual work - track how many jobs were processed
        jobs_checked = await checker.process_jobs()
        if isinstance(jobs_checked, int):
            items_processed = jobs_checked
        
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        logger.info(f"=== Kodosumi status checking completed in {duration:.2f} seconds ===")
        
    except Exception as e:
        error = str(e)
        logger.error(f"Error in Kodosumi status checking: {e}")
        raise
        
    finally:
        # Always log completion, whether successful or not
        await cron_logger.log_completion(items_processed, error)

if __name__ == "__main__":
    # Implement file-based locking to prevent overlapping runs
    lock_file_path = '/tmp/kodosumi_status_checker.lock'
    lock_file = None
    
    try:
        # Open lock file
        lock_file = open(lock_file_path, 'w')
        
        # Try to acquire an exclusive lock (non-blocking)
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info("Lock acquired, running Kodosumi Status Checker...")
        
        try:
            asyncio.run(run_once())
        except Exception as e:
            logger.error(f"Kodosumi Status Checker failed: {e}")
            sys.exit(1)
            
    except IOError:
        # Could not acquire lock, another instance is running
        logger.warning("Another instance of Kodosumi Status Checker is already running, skipping this run")
        sys.exit(0)
    finally:
        # Release lock and close file
        if lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
                lock_file.close()
            except:
                pass
