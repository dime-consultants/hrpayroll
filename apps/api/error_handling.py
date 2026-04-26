"""
Production-grade error handling and circuit breaker for LMS repayment engine.

Classifies errors into three categories:
  1. Network errors (DNS, connection timeouts, temporary failures)
  2. Transient errors (5xx, 429 rate limit — retry with backoff)
  3. Business errors (4xx validation, no balance — don't retry)

Implements circuit breaker to prevent retry storms when LMS is down.
"""
import logging
import time
from enum import Enum
from datetime import timedelta
from decimal import Decimal

import requests
from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger(__name__)


class ErrorClassification(Enum):
    """Categorize errors for intelligent retry logic."""
    NETWORK = 'network'           # DNS failure, connection timeout, connection refused
    TRANSIENT = 'transient'       # 5xx, 429, temporary unavailability
    BUSINESS = 'business'         # 4xx (except 429), validation, insufficient balance
    AUTH = 'auth'                 # 401, 403, invalid credentials
    UNKNOWN = 'unknown'


class CircuitBreakerState(Enum):
    CLOSED = 'closed'       # Normal operation
    OPEN = 'open'           # Reject requests (LMS is down)
    HALF_OPEN = 'half_open' # Testing if LMS recovered


class LMSCircuitBreaker:
    """
    Circuit breaker for LMS endpoint.
    
    Prevents retry storms when LMS is unavailable:
    
    CLOSED (normal) →
      Consecutive failures: 5 → OPEN (reject new requests)
    
    OPEN (reject) →
      Wait 5 minutes → HALF_OPEN (allow 1 test request)
    
    HALF_OPEN (testing) →
      Success → CLOSED (resume normal)
      Failure → OPEN (wait another 5 minutes)
    """
    
    def __init__(self, endpoint: str = 'lms_repayment'):
        self.endpoint = endpoint
        self.failure_threshold = 5        # failures before opening circuit
        self.recovery_timeout = 300       # seconds (5 minutes)
        self.success_threshold = 2        # consecutive successes to close circuit
    
    def _state_key(self) -> str:
        return f'circuit_breaker:{self.endpoint}:state'
    
    def _failure_key(self) -> str:
        return f'circuit_breaker:{self.endpoint}:failures'
    
    def _success_key(self) -> str:
        return f'circuit_breaker:{self.endpoint}:successes'
    
    def _open_since_key(self) -> str:
        return f'circuit_breaker:{self.endpoint}:open_since'
    
    def get_state(self) -> CircuitBreakerState:
        """Get current circuit breaker state."""
        state_str = cache.get(self._state_key(), CircuitBreakerState.CLOSED.value)
        return CircuitBreakerState(state_str)
    
    def is_open(self) -> bool:
        """Check if circuit is open (reject requests)."""
        state = self.get_state()
        
        if state == CircuitBreakerState.CLOSED:
            return False
        
        if state == CircuitBreakerState.OPEN:
            # Check if recovery timeout has elapsed
            open_since = cache.get(self._open_since_key())
            if open_since and (time.time() - float(open_since)) > self.recovery_timeout:
                # Try to recover
                self._set_half_open()
                return False
            return True
        
        # HALF_OPEN: allow request through for testing
        return False
    
    def record_success(self) -> None:
        """Record a successful request."""
        state = self.get_state()
        
        # Clear failure counter
        cache.delete(self._failure_key())
        
        if state == CircuitBreakerState.HALF_OPEN:
            # Increment success counter
            successes = int(cache.get(self._success_key(), 0)) + 1
            cache.set(self._success_key(), successes, timeout=self.recovery_timeout)
            
            if successes >= self.success_threshold:
                # Circuit recovered, close it
                self._set_closed()
                log.info('Circuit breaker CLOSED — LMS endpoint recovered')
        elif state == CircuitBreakerState.CLOSED:
            # Already closed, just clear any lingering success counter
            cache.delete(self._success_key())
    
    def record_failure(self) -> None:
        """Record a failed request."""
        failures = int(cache.get(self._failure_key(), 0)) + 1
        cache.set(self._failure_key(), failures, timeout=self.recovery_timeout * 2)
        
        if failures >= self.failure_threshold:
            self._set_open()
            log.critical(
                'Circuit breaker OPEN — LMS endpoint unavailable after %d failures',
                failures
            )
    
    def _set_closed(self) -> None:
        cache.set(self._state_key(), CircuitBreakerState.CLOSED.value, timeout=self.recovery_timeout * 10)
        cache.delete(self._failure_key())
        cache.delete(self._success_key())
        cache.delete(self._open_since_key())
    
    def _set_open(self) -> None:
        cache.set(self._state_key(), CircuitBreakerState.OPEN.value, timeout=self.recovery_timeout * 10)
        cache.set(self._open_since_key(), str(time.time()), timeout=self.recovery_timeout * 10)
        cache.delete(self._success_key())
    
    def _set_half_open(self) -> None:
        cache.set(self._state_key(), CircuitBreakerState.HALF_OPEN.value, timeout=self.recovery_timeout)
        cache.delete(self._failure_key())
        cache.delete(self._success_key())


def classify_request_error(exc: Exception) -> ErrorClassification:
    """
    Classify a requests exception for intelligent retry strategy.
    
    Network errors (retry with exponential backoff):
      - ConnectionError
      - Timeout
      - NameResolutionError (DNS)
      - ProxyError
    
    Transient errors (retry with backoff):
      - HTTP 500, 502, 503, 504
      - HTTP 429 (rate limit)
    
    Business/auth errors (don't retry):
      - HTTP 400, 401, 403, 404
      - Insufficient balance
      - Invalid phone number
    """
    if isinstance(exc, (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ProxyError,
    )):
        log.warning('Network error detected: %s', type(exc).__name__)
        return ErrorClassification.NETWORK
    
    if isinstance(exc, requests.exceptions.RequestException):
        # Check if it's a response-based error
        if hasattr(exc, 'response') and exc.response is not None:
            status = exc.response.status_code
            
            if status == 401:
                return ErrorClassification.AUTH
            elif status == 403:
                return ErrorClassification.BUSINESS
            elif status in (500, 502, 503, 504):
                return ErrorClassification.TRANSIENT
            elif status == 429:
                return ErrorClassification.TRANSIENT
            elif status >= 400:
                return ErrorClassification.BUSINESS
        
        # Generic network error (likely DNS or connection)
        if 'NameResolutionError' in str(exc) or 'name resolution' in str(exc).lower():
            log.warning('DNS resolution error: %s', exc)
            return ErrorClassification.NETWORK
    
    log.warning('Unknown error type: %s — %s', type(exc).__name__, exc)
    return ErrorClassification.UNKNOWN


def classify_lms_response_error(response_code: str, response_body: dict) -> ErrorClassification:
    """
    Classify LMS API response errors.
    
    LMS response codes:
      - 200.xxx: success
      - 400.xxx: validation error (don't retry)
      - 500.xxx: server error (retry)
    """
    if response_code.startswith('200'):
        return ErrorClassification.BUSINESS  # Actually success, shouldn't classify as error
    
    if response_code.startswith('400'):
        # Check for specific business errors
        message = response_body.get('message', '').lower()
        if 'no balance' in message or 'insufficient' in message:
            return ErrorClassification.BUSINESS
        return ErrorClassification.BUSINESS
    
    if response_code.startswith('401') or response_code.startswith('403'):
        return ErrorClassification.AUTH
    
    if response_code.startswith('500'):
        return ErrorClassification.TRANSIENT
    
    return ErrorClassification.UNKNOWN


def get_retry_countdown(
    attempt: int,
    classification: ErrorClassification,
) -> int:
    """
    Compute retry countdown (seconds) based on error type and attempt number.
    
    Network errors: exponential backoff (aggressive)
      attempt 1 → 30s
      attempt 2 → 60s
      attempt 3 → 120s
    
    Transient errors: exponential with longer base (medium)
      attempt 1 → 60s
      attempt 2 → 120s
      attempt 3 → 300s (5 min)
    
    Business/auth errors: don't retry
    """
    if classification in (ErrorClassification.BUSINESS, ErrorClassification.AUTH):
        return -1  # Signal: don't retry
    
    if classification == ErrorClassification.NETWORK:
        # Aggressive retry for network (likely transient)
        base_countdown = 30
        countdown = base_countdown * (2 ** (attempt - 1))
        return min(countdown, 300)  # Cap at 5 minutes
    
    if classification == ErrorClassification.TRANSIENT:
        # Slower retry for transient (server problems)
        base_countdown = 60
        countdown = base_countdown * (2 ** (attempt - 1))
        return min(countdown, 600)  # Cap at 10 minutes
    
    # UNKNOWN: treat as transient
    return get_retry_countdown(attempt, ErrorClassification.TRANSIENT)


class LMSException(Exception):
    """Base exception for LMS errors."""
    def __init__(self, classification: ErrorClassification, message: str, response_body: dict = None):
        self.classification = classification
        self.message = message
        self.response_body = response_body or {}
        super().__init__(message)


class LMSNetworkError(LMSException):
    """Network connectivity issue with LMS."""
    def __init__(self, message: str):
        super().__init__(ErrorClassification.NETWORK, message)


class LMSTransientError(LMSException):
    """Temporary LMS unavailability (5xx, 429)."""
    def __init__(self, message: str, response_body: dict = None):
        super().__init__(ErrorClassification.TRANSIENT, message, response_body)


class LMSBusinessError(LMSException):
    """Business logic error (invalid phone, no balance, etc)."""
    def __init__(self, message: str, response_body: dict = None):
        super().__init__(ErrorClassification.BUSINESS, message, response_body)


class LMSAuthError(LMSException):
    """Authentication or authorization failure."""
    def __init__(self, message: str):
        super().__init__(ErrorClassification.AUTH, message)
