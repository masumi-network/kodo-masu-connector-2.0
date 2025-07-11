"""
Circuit breaker implementation for external API calls.
"""
import asyncio
import time
from enum import Enum
from typing import Optional, Callable, Any
import logging
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    """Circuit breaker for protecting against cascading failures."""
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: type = Exception
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = None
        self._success_count = 0
        
    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        if self._state == CircuitState.OPEN:
            if self._last_failure_time and \
               (time.time() - self._last_failure_time) > self.recovery_timeout:
                logger.info(f"Circuit breaker '{self.name}' transitioning to HALF_OPEN")
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
        return self._state
    
    def record_success(self):
        """Record a successful call."""
        self._failure_count = 0
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= 3:  # Require 3 successful calls to close
                logger.info(f"Circuit breaker '{self.name}' transitioning to CLOSED")
                self._state = CircuitState.CLOSED
    
    def record_failure(self):
        """Record a failed call."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        
        if self._failure_count >= self.failure_threshold:
            logger.warning(f"Circuit breaker '{self.name}' transitioning to OPEN")
            self._state = CircuitState.OPEN
        
        if self._state == CircuitState.HALF_OPEN:
            logger.warning(f"Circuit breaker '{self.name}' transitioning back to OPEN")
            self._state = CircuitState.OPEN
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        if self.state == CircuitState.OPEN:
            raise Exception(f"Circuit breaker '{self.name}' is OPEN")
        
        try:
            result = await func(*args, **kwargs)
            self.record_success()
            return result
        except self.expected_exception as e:
            self.record_failure()
            raise

class PaymentServiceCircuitBreaker:
    """Circuit breaker specifically for payment service calls."""
    
    def __init__(self):
        self.circuit_breaker = CircuitBreaker(
            name="payment_service",
            failure_threshold=3,
            recovery_timeout=30.0,
            expected_exception=Exception
        )
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception)
    )
    async def call_with_retry(self, func: Callable, *args, **kwargs) -> Any:
        """Call function with circuit breaker and retry logic."""
        return await self.circuit_breaker.call(func, *args, **kwargs)
    
    def get_status(self) -> dict:
        """Get circuit breaker status."""
        return {
            "state": self.circuit_breaker.state.value,
            "failure_count": self.circuit_breaker._failure_count,
            "last_failure_time": self.circuit_breaker._last_failure_time
        }