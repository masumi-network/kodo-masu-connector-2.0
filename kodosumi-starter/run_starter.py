#!/usr/bin/env python3
"""
Wrapper script to run the Kodosumi starter service once.
This script is designed to be called by cron every minute.
"""
import asyncio
import sys
import os
import fcntl
from datetime import datetime

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from kodosumi_starter import KodosumiStarter
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def run_once():
    """Run the Kodosumi starter once."""
    starter = KodosumiStarter()
    
    try:
        logger.info("=== Starting Kodosumi job processing ===")
        start_time = datetime.utcnow()
        
        await starter.process_jobs()
        
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        logger.info(f"=== Kodosumi job processing completed in {duration:.2f} seconds ===")
        
    except Exception as e:
        logger.error(f"Error in Kodosumi job processing: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # Implement file-based locking to prevent overlapping runs
    lock_file_path = '/tmp/kodosumi_starter.lock'
    lock_file = None
    
    try:
        # Open lock file
        lock_file = open(lock_file_path, 'w')
        
        # Try to acquire an exclusive lock (non-blocking)
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info("Lock acquired, running Kodosumi Job Starter...")
        
        try:
            asyncio.run(run_once())
        except Exception as e:
            logger.error(f"Kodosumi Job Starter failed: {e}")
            sys.exit(1)
            
    except IOError:
        # Could not acquire lock, another instance is running
        logger.warning("Another instance of Kodosumi Job Starter is already running, skipping this run")
        sys.exit(0)
    finally:
        # Release lock and close file
        if lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
                lock_file.close()
            except:
                pass