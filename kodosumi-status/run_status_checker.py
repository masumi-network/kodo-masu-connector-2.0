#!/usr/bin/env python3
"""
Wrapper script to run the Kodosumi status checker service once.
This script is designed to be called by cron every 2 minutes.
"""
import asyncio
import sys
import os
import fcntl
from datetime import datetime

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from kodosumi_status import KodosumiStatusChecker
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def run_once():
    """Run the Kodosumi status checker once."""
    checker = KodosumiStatusChecker()
    
    try:
        logger.info("=== Starting Kodosumi status checking ===")
        start_time = datetime.utcnow()
        
        await checker.process_jobs()
        
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        logger.info(f"=== Kodosumi status checking completed in {duration:.2f} seconds ===")
        
    except Exception as e:
        logger.error(f"Error in Kodosumi status checking: {e}")
        sys.exit(1)

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