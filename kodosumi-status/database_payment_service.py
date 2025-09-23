#!/usr/bin/env python3
"""
Database-based Payment Service for Kodosumi Status Checker

This module handles payment completion using configuration from the database
instead of environment variables.
"""
import logging
import json
from typing import Dict, Any, Optional
from masumi.payment import Payment
from masumi.config import Config as MasumiConfig

logger = logging.getLogger(__name__)


class DatabasePaymentService:
    """Payment service that reads configuration from database."""
    
    @staticmethod
    def extract_payment_config(payment_data: Dict[str, Any]) -> Dict[str, str]:
        """
        Extract payment configuration from payment_data.
        
        Returns dict with:
        - payment_service_url
        - network
        """
        config = {}
        
        # Extract from nested data structure
        data = payment_data.get('data', {})
        
        # Get network from PaymentSource
        payment_source = data.get('PaymentSource', {})
        network = payment_source.get('network', 'Mainnet')
        config['network'] = network
        
        # Get payment service URL from environment or use the one from payment response
        import os
        env_payment_url = os.getenv('PAYMENT_SERVICE_URL')
        
        # Use environment variable if available, otherwise determine by network
        if env_payment_url:
            config['payment_service_url'] = env_payment_url
        elif network == 'Preprod':
            config['payment_service_url'] = 'https://api-preprod.masumi.solutions'
        else:
            # Default to mainnet
            config['payment_service_url'] = 'https://api.masumi.solutions'
        
        return config
    
    @staticmethod
    async def complete_payment(
        job: Dict[str, Any],
        final_result: str,
        api_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Complete a payment using database configuration.
        
        Args:
            job: Job data including payment_data, input_data, agent_identifier
            final_result: The final result from Kodosumi
            api_key: API key from the database
            
        Returns:
            Payment completion response or None if failed
        """
        try:
            job_id = str(job['job_id'])
            
            # Extract payment configuration
            payment_config = DatabasePaymentService.extract_payment_config(job['payment_data'])
            
            # Extract blockchain identifier
            blockchain_identifier = DatabasePaymentService._extract_blockchain_identifier(job['payment_data'])
            if not blockchain_identifier:
                logger.warning(f"Job {job_id} has no blockchain identifier, cannot complete payment")
                return None
            
            # Parse the final result
            try:
                result_data = json.loads(final_result)
            except json.JSONDecodeError:
                result_data = {"result": final_result}
            
            # Create Masumi config
            masumi_config = MasumiConfig(
                payment_service_url=payment_config['payment_service_url'],
                payment_api_key=api_key
            )
            
            logger.info(f"Completing payment for job {job_id}:")
            logger.info(f"  - Blockchain ID: {blockchain_identifier[:20]}...")
            logger.info(f"  - Network: {payment_config['network']}")
            logger.info(f"  - Service URL: {payment_config['payment_service_url']}")
            logger.info(f"  - Agent: {job['agent_identifier'][:20]}...")
            
            # Create payment instance
            agent_identifier = job.get('agent_identifier')
            payment = Payment(
                agent_identifier=agent_identifier,
                config=masumi_config,
                network=payment_config['network'],
                identifier_from_purchaser=job['identifier_from_purchaser'],
                input_data=job['input_data']
            )
            
            # Complete the payment
            response = await payment.complete_payment(blockchain_identifier, result_data)
            
            logger.info(f"Successfully completed payment for job {job_id}. Response: {response}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to complete payment for job {job['job_id']}: {e}")
            logger.error(f"This means the agent may not receive payment for this completed job!")
            return None
    
    @staticmethod
    def _extract_blockchain_identifier(payment_data: Dict[str, Any]) -> Optional[str]:
        """Extract blockchain identifier from payment data."""
        # Check nested structure first (new format)
        if 'data' in payment_data and isinstance(payment_data['data'], dict):
            return payment_data['data'].get('blockchainIdentifier')
        
        # Fallback to direct access (old format)
        return payment_data.get('blockchainIdentifier')
