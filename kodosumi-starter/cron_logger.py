#!/usr/bin/env python3
"""
Shared module for logging cron job executions to the database.
This ensures all cron jobs are tracked consistently.
"""
import asyncio
import asyncpg
import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class CronExecutionLogger:
    """Logs cron job executions to the database."""
    
    def __init__(self, service_name: str, database_url: str):
        self.service_name = service_name
        self.database_url = database_url
        self.start_time = None
        self.execution_id = None
    
    async def log_start(self) -> int:
        """Log the start of a cron execution. Returns execution ID."""
        self.start_time = time.time()
        
        try:
            conn = await asyncpg.connect(self.database_url)
            
            # Insert execution record
            self.execution_id = await conn.fetchval(
                """
                INSERT INTO cron_executions (service_name, status, execution_time)
                VALUES ($1, 'started', CURRENT_TIMESTAMP)
                RETURNING id
                """,
                self.service_name
            )
            
            await conn.close()
            logger.info(f"Logged cron execution start for {self.service_name} (ID: {self.execution_id})")
            return self.execution_id
            
        except Exception as e:
            logger.error(f"Failed to log cron execution start: {e}")
            # Don't fail the cron job if logging fails
            return None
    
    async def log_completion(self, items_processed: int = 0, error: Optional[str] = None):
        """Log the completion of a cron execution."""
        if not self.execution_id:
            return
        
        duration_ms = int((time.time() - self.start_time) * 1000) if self.start_time else None
        status = 'failed' if error else 'completed'
        
        try:
            conn = await asyncpg.connect(self.database_url)
            
            await conn.execute(
                """
                UPDATE cron_executions 
                SET status = $1, 
                    items_processed = $2,
                    error_message = $3,
                    duration_ms = $4
                WHERE id = $5
                """,
                status, items_processed, error, duration_ms, self.execution_id
            )
            
            await conn.close()
            logger.info(f"Logged cron execution completion for {self.service_name} "
                       f"(ID: {self.execution_id}, Status: {status}, Items: {items_processed})")
            
        except Exception as e:
            logger.error(f"Failed to log cron execution completion: {e}")


# Decorator for easy use
def track_cron_execution(service_name: str, database_url: str):
    """Decorator to automatically track cron executions."""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            logger_instance = CronExecutionLogger(service_name, database_url)
            await logger_instance.log_start()
            
            items_processed = 0
            error = None
            
            try:
                # Run the actual cron job
                result = await func(*args, **kwargs)
                
                # Try to extract items_processed from result if it's a dict
                if isinstance(result, dict) and 'items_processed' in result:
                    items_processed = result['items_processed']
                elif isinstance(result, int):
                    items_processed = result
                    
                return result
                
            except Exception as e:
                error = str(e)
                raise
                
            finally:
                await logger_instance.log_completion(items_processed, error)
        
        return wrapper
    return decorator