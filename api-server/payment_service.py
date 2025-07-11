from masumi.payment import Payment
from masumi.config import Config as MasumiConfig
from typing import Dict, Any, Optional
import logging
import asyncio
from config import config
from circuit_breaker import PaymentServiceCircuitBreaker

logger = logging.getLogger(__name__)

class PaymentService:
    """Service for handling Masumi payments with circuit breaker and retry logic."""
    
    def __init__(self):
        self.masumi_config = MasumiConfig(
            payment_service_url=config.PAYMENT_SERVICE_URL,
            payment_api_key=config.PAYMENT_API_KEY
        )
        self.circuit_breaker = PaymentServiceCircuitBreaker()
        self.timeout = 30.0  # 30 second timeout for payment operations
    
    async def create_payment_request(self, 
                                   identifier_from_purchaser: str,
                                   input_data: Dict[str, Any],
                                   agent_identifier: Optional[str] = None,
                                   metadata: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a payment request using the Masumi payment service.
        
        Args:
            identifier_from_purchaser: Unique identifier from the purchaser
            input_data: Input data for the job
            agent_identifier: Optional agent identifier (uses flow-specific or falls back to config)
            metadata: Optional metadata for the payment
            
        Returns:
            Payment response data
        """
        try:
            # Use provided agent_identifier or fall back to config
            agent_id = agent_identifier or config.AGENT_IDENTIFIER
            
            # Create payment instance
            payment = Payment(
                agent_identifier=agent_id,
                config=self.masumi_config,
                network=config.NETWORK,
                identifier_from_purchaser=identifier_from_purchaser,
                input_data=input_data
            )
            
            # Create payment request with circuit breaker and timeout
            async def _create_request():
                return await asyncio.wait_for(
                    payment.create_payment_request(metadata=metadata),
                    timeout=self.timeout
                )
            
            response = await self.circuit_breaker.call_with_retry(_create_request)
            
            logger.info(f"Payment request created for purchaser: {identifier_from_purchaser}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to create payment request: {e}")
            raise
    
    async def complete_payment(self, blockchain_identifier: str, 
                             job_output: Dict[str, Any],
                             identifier_from_purchaser: str,
                             input_data: Dict[str, Any],
                             agent_identifier: Optional[str] = None) -> Dict[str, Any]:
        """
        Complete a payment by submitting the result.
        
        Args:
            blockchain_identifier: The blockchain identifier of the payment
            job_output: The result of the job
            identifier_from_purchaser: Identifier from purchaser
            input_data: Original input data
            agent_identifier: Optional agent identifier (uses flow-specific or falls back to config)
            
        Returns:
            Payment completion response
        """
        try:
            # Use provided agent_identifier or fall back to config
            agent_id = agent_identifier or config.AGENT_IDENTIFIER
            
            # Create payment instance with same parameters as original
            payment = Payment(
                agent_identifier=agent_id,
                config=self.masumi_config,
                network=config.NETWORK,
                identifier_from_purchaser=identifier_from_purchaser,
                input_data=input_data
            )
            
            # Complete the payment with circuit breaker and timeout
            async def _complete_payment():
                return await asyncio.wait_for(
                    payment.complete_payment(blockchain_identifier, job_output),
                    timeout=self.timeout
                )
            
            response = await self.circuit_breaker.call_with_retry(_complete_payment)
            
            logger.info(f"Payment completed for blockchain identifier: {blockchain_identifier}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to complete payment: {e}")
            raise

    def get_circuit_breaker_status(self) -> Dict[str, Any]:
        """Get the current status of the circuit breaker."""
        return self.circuit_breaker.get_status()

# Global payment service instance
payment_service = PaymentService()