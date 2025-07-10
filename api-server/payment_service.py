from masumi.payment import Payment
from masumi.config import Config as MasumiConfig
from typing import Dict, Any, Optional
import logging
from config import config

logger = logging.getLogger(__name__)

class PaymentService:
    """Service for handling Masumi payments."""
    
    def __init__(self):
        self.masumi_config = MasumiConfig(
            payment_service_url=config.PAYMENT_SERVICE_URL,
            payment_api_key=config.PAYMENT_API_KEY
        )
    
    async def create_payment_request(self, 
                                   identifier_from_purchaser: str,
                                   input_data: Dict[str, Any],
                                   metadata: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a payment request using the Masumi payment service.
        
        Args:
            identifier_from_purchaser: Unique identifier from the purchaser
            input_data: Input data for the job
            metadata: Optional metadata for the payment
            
        Returns:
            Payment response data
        """
        try:
            # Create payment instance
            payment = Payment(
                agent_identifier=config.AGENT_IDENTIFIER,
                config=self.masumi_config,
                network=config.NETWORK,
                identifier_from_purchaser=identifier_from_purchaser,
                input_data=input_data
            )
            
            # Create payment request
            response = await payment.create_payment_request(metadata=metadata)
            
            logger.info(f"Payment request created for purchaser: {identifier_from_purchaser}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to create payment request: {e}")
            raise
    
    async def complete_payment(self, blockchain_identifier: str, 
                             job_output: Dict[str, Any],
                             identifier_from_purchaser: str,
                             input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Complete a payment by submitting the result.
        
        Args:
            blockchain_identifier: The blockchain identifier of the payment
            job_output: The result of the job
            identifier_from_purchaser: Identifier from purchaser
            input_data: Original input data
            
        Returns:
            Payment completion response
        """
        try:
            # Create payment instance with same parameters as original
            payment = Payment(
                agent_identifier=config.AGENT_IDENTIFIER,
                config=self.masumi_config,
                network=config.NETWORK,
                identifier_from_purchaser=identifier_from_purchaser,
                input_data=input_data
            )
            
            # Complete the payment
            response = await payment.complete_payment(blockchain_identifier, job_output)
            
            logger.info(f"Payment completed for blockchain identifier: {blockchain_identifier}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to complete payment: {e}")
            raise

# Global payment service instance
payment_service = PaymentService()