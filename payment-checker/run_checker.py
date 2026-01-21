#!/usr/bin/env python3
import asyncio
import logging
import signal
import sys
import time
from payment_checker import PaymentChecker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PaymentCheckerService:
    """Service wrapper that runs payment checker every minute."""
    
    def __init__(self):
        self.running = True
        self.checker = PaymentChecker()
        
        # Handle shutdown signals
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
    
    async def run(self):
        """Main service loop - run payment checker every 30 seconds."""
        logger.info("Payment Checker Service started - checking every 30 seconds")

        while self.running:
            try:
                start_time = time.time()

                logger.info("Starting payment check cycle...")
                await self.checker.process_payments()

                elapsed = time.time() - start_time
                logger.info(f"Payment check cycle completed in {elapsed:.2f} seconds")

                # Wait for 30 seconds before next check
                if self.running:
                    logger.info("Waiting 30 seconds before next check...")
                    await asyncio.sleep(30)
                    
            except Exception as e:
                logger.error(f"Error in payment checker service: {e}")
                # Wait a bit before retrying on error
                if self.running:
                    logger.info("Waiting 30 seconds before retry due to error...")
                    await asyncio.sleep(30)
        
        logger.info("Payment Checker Service stopped")

async def main():
    """Main entry point."""
    service = PaymentCheckerService()
    await service.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Service interrupted by user")
    except Exception as e:
        logger.error(f"Service failed: {e}")
        sys.exit(1)