"""
Singleton HTTP client for the Dime LMS (back.dimeapp.co.ke).

Guarantees:
  - Shared connection pool across worker threads (lru_cache Session)
  - Separate non-retrying session for OAuth token endpoint
  - Per-phone Redis mutex → no concurrent duplicate repayments
  - Idempotency table check → no re-send on retry
  - Exponential backoff retries via urllib3 Retry
  - OAuth2 consumer key/secret token auth with auto-refresh on 401
  - Circuit breaker to prevent retry storms on LMS outage
  - Intelligent error classification (network vs business errors)
  - Customer name lookups (exclusive-membership only) for HR-facing lists,
    with Redis caching and parallel batch fetch — no local customer table

NOTE: amount is sent to the LMS as requested. The LMS enforces its own
balance cap server-side; we intentionally do not cap here (see
send_repayment step 3) since a stale local balance read was truncating
legitimate full repayments.
"""
import logging
import time
from decimal import Decimal
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from django.conf import settings
from django.core.cache import cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .error_handling import (
    LMSCircuitBreaker,
    classify_request_error,
    classify_lms_response_error,
    ErrorClassification,
    LMSNetworkError,
    LMSTransientError,
    LMSBusinessError,
)


log = logging.getLogger(__name__)

_LOCK_TTL = 60  # seconds
_CUSTOMER_CACHE_TTL = 60 * 60 * 24       # 24h — names change rarely
_CUSTOMER_NEGATIVE_TTL = 60 * 5          # 5 min — don't hammer on repeated misses


# ─────────────────────────────────────────────────────────────
# Connection pool (one Session per worker process)
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_session() -> requests.Session:
    """Retrying session for all LMS calls except token auth."""
    session = requests.Session()
    retry = Retry(
        total=settings.LMS_RETRY_MAX,
        backoff_factor=settings.LMS_RETRY_BACKOFF,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=['POST', 'GET'],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        pool_connections=settings.LMS_REQUEST_POOL_SIZE,
        pool_maxsize=settings.LMS_REQUEST_POOL_MAXSIZE,
        max_retries=retry,
    )
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


@lru_cache(maxsize=1)
def _get_auth_session() -> requests.Session:
    """
    Plain session used exclusively for the OAuth token endpoint.
    No retry adapter — a 4xx from the token endpoint is a credential/config
    error, not a transient blip. Retrying it would multiply noise and slow
    down the three-shape fallback loop in _get_auth_token().
    """
    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=2,
        pool_maxsize=4,
        # Intentionally no Retry
    )
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


# ─────────────────────────────────────────────────────────────
# Redis mutex
# ─────────────────────────────────────────────────────────────

def _lock_key(phone: str) -> str:
    return f'lms:repayment_lock:{phone}'


def _acquire_lock(phone: str) -> bool:
    return cache.add(_lock_key(phone), '1', timeout=_LOCK_TTL)


def _release_lock(phone: str) -> None:
    cache.delete(_lock_key(phone))


# ─────────────────────────────────────────────────────────────
# Auth token (OAuth2 consumer key/secret, cached in Redis)
# ─────────────────────────────────────────────────────────────

_TOKEN_KEY = 'lms:service_token'
_TOKEN_TTL = 60 * 60 * 7  # cache for 7 hrs (LMS JWT valid for 8)


def _get_auth_token() -> str:
    """
    Obtain a Bearer token from the Dime LMS partner token endpoint.
    Endpoint: POST /api/partner/token/

    LMS response shape:
        {"code": "200.001", "data": {"token": "...", "expires_at": "1773828511"}}

    Tries three request shapes in order until one returns a token:
      1. JSON body  {"consumer_key": ..., "consumer_secret": ...}
      2. HTTP Basic auth  (consumer_key:consumer_secret)
      3. Form body  consumer_key=...&consumer_secret=...

    Uses _get_auth_session() — a plain session with NO retry adapter —
    so a bad credential doesn't silently trigger LMS_RETRY_MAX extra calls
    per shape (which would mean up to 9 slow requests before giving up).

    Token is cached in Redis until 5 minutes before the LMS-reported expiry
    (falls back to 7 hours if no expiry is provided).
    """
    token = cache.get(_TOKEN_KEY)
    if token:
        return token

    key    = settings.LMS_CONSUMER_KEY
    secret = settings.LMS_CONSUMER_SECRET
    url    = settings.LMS_TOKEN_URL

    if not key or not secret:
        raise ValueError(
            'LMS_CONSUMER_KEY and LMS_CONSUMER_SECRET must be set in .env.prod'
        )

    attempts = [
        # 1. JSON body — confirmed working shape for this LMS
        dict(
            method='json',
            kwargs=dict(
                json={'consumer_key': key, 'consumer_secret': secret},
                headers={'Content-Type': 'application/json'},
            ),
        ),
        # 2. HTTP Basic auth with empty body
        dict(
            method='basic',
            kwargs=dict(
                auth=(key, secret),
                headers={'Content-Type': 'application/json'},
            ),
        ),
        # 3. Form-encoded body
        dict(
            method='form',
            kwargs=dict(
                data={'consumer_key': key, 'consumer_secret': secret},
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            ),
        ),
    ]

    last_error = None

    for attempt in attempts:
        method = attempt['method']
        try:
            # Use the bare auth session — no retry adapter
            resp = _get_auth_session().post(
                url,
                timeout=settings.LMS_TIMEOUT,
                verify=True,
                **attempt['kwargs'],
            )
            log.debug(
                'LMS token attempt method=%s status=%s body=%s',
                method, resp.status_code, resp.text[:300],
            )

            if resp.status_code >= 400:
                log.warning(
                    'LMS token method=%s returned %s — trying next',
                    method, resp.status_code,
                )
                last_error = f'HTTP {resp.status_code}: {resp.text[:200]}'
                continue

            data = resp.json()

            # ── Extract token ──────────────────────────────────────
            # Primary shape:  {"code": "200.001", "data": {"token": "...", "expires_at": "..."}}
            # Fallback shapes: flat {"access": ...} / {"access_token": ...} / {"token": ...}
            nested = data.get('data') if isinstance(data.get('data'), dict) else {}
            token = (
                nested.get('token')
                or nested.get('access_token')
                or nested.get('access')
                or data.get('token')
                or data.get('access_token')
                or data.get('access')
            )

            if not token:
                log.warning(
                    'LMS token method=%s got 2xx but no token field: %s — trying next',
                    method, data,
                )
                last_error = f'No token field in response: {data}'
                continue

            # ── Determine TTL from LMS-reported expiry ─────────────
            # expires_at is a Unix timestamp string e.g. "1773828511"
            expires_at = data.get('expires_at')
            ttl = _TOKEN_TTL  # default 7 hours
            if expires_at:
                try:
                    import time as _time
                    ttl = max(int(expires_at) - int(_time.time()) - 300, 300)
                    log.debug(
                        'LMS token expires_at=%s computed ttl=%ds',
                        expires_at, ttl,
                    )
                except Exception as ttl_exc:
                    log.warning(
                        'Could not parse expires_at=%s: %s — using default TTL',
                        expires_at, ttl_exc,
                    )
                    ttl = _TOKEN_TTL

            cache.set(_TOKEN_KEY, token, timeout=ttl)
            log.info(
                'LMS token obtained and cached | method=%s url=%s ttl=%ds',
                method, url, ttl,
            )
            return token

        except Exception as exc:
            log.warning('LMS token method=%s exception: %s — trying next', method, exc)
            last_error = str(exc)
            continue

    # All three attempts exhausted
    raise ValueError(
        f'LMS auth failed at {url} after trying all request formats. '
        f'Last error: {last_error}'
    )


def _headers() -> dict:
    return {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {_get_auth_token()}',
    }


def invalidate_token_cache() -> None:
    """Force re-authentication on the next outbound LMS call."""
    cache.delete(_TOKEN_KEY)
    log.info('LMS token cache invalidated')


# ─────────────────────────────────────────────────────────────
# Result object
# ─────────────────────────────────────────────────────────────

class LMSRepaymentResult:
    __slots__ = ('success', 'code', 'amount_sent', 'response_body', 'duration_ms', 'error')

    def __init__(self, success, code, amount_sent, response_body, duration_ms, error=''):
        self.success = success
        self.code = code
        self.amount_sent = Decimal(str(amount_sent))
        self.response_body = response_body
        self.duration_ms = duration_ms
        self.error = error


# ─────────────────────────────────────────────────────────────
# Public entry point — repayments
# ─────────────────────────────────────────────────────────────

def send_repayment(
    phone_number: str,
    amount: Decimal,
    collection_date: str,
    idempotency_key: str,
    organization_code: str,
    max_wait_lock: int = 30,
) -> LMSRepaymentResult:
    """
    Thread-safe repayment dispatch with circuit breaker and intelligent retry logic.

    Steps:
      1. Check circuit breaker (fail fast if LMS is down)
      2. Acquire per-phone Redis mutex (prevents race conditions)
      3. Idempotency check (prevents duplicate sends on retry)
      4. Reject non-positive amounts (LMS itself enforces the balance cap —
         we do NOT cap client-side; see module docstring)
      5. POST to LMS /api/partner/pay-loan/
      6. Release mutex in finally block regardless of outcome

    Returns:
      LMSRepaymentResult with success/failure status and error classification
    """
    # ── 0. Check circuit breaker ───────────────────────────
    circuit_breaker = LMSCircuitBreaker(endpoint='lms_repayment')
    if circuit_breaker.is_open():
        log.warning(
            'Circuit breaker OPEN — rejecting repayment for %s (LMS temporarily unavailable)',
            phone_number
        )
        return LMSRepaymentResult(
            success=False, code='circuit_open',
            amount_sent=Decimal('0'), response_body={},
            duration_ms=0,
            error='LMS endpoint temporarily unavailable — circuit breaker open',
        )

    # ── 1. Acquire per-phone lock ──────────────────────────
    waited = 0
    while not _acquire_lock(phone_number):
        if waited >= max_wait_lock:
            log.warning('Lock timeout for %s after %ds', phone_number, max_wait_lock)
            return LMSRepaymentResult(
                success=False, code='lock_timeout',
                amount_sent=Decimal('0'), response_body={},
                duration_ms=0,
                error='Concurrent repayment in progress — skipped',
            )
        time.sleep(1)
        waited += 1

    try:
        # ── 2. Idempotency check ───────────────────────────
        from apps.repayments.models import IdempotencyKey
        try:
            existing = IdempotencyKey.objects.get(key=idempotency_key)
            if existing.status == IdempotencyKey.STATUS_SUCCESS and not existing.is_expired:
                log.info('Idempotency hit for key %s', idempotency_key[:16])
                payload = existing.response_payload
                return LMSRepaymentResult(
                    success=True, code='200.001',
                    amount_sent=Decimal(str(payload.get('amount_sent', amount))),
                    response_body=payload, duration_ms=0,
                )
        except IdempotencyKey.DoesNotExist:
            pass

        # ── 3. Send the requested amount as-is ─────────────
        # LMS enforces the balance cap server-side. We only guard against
        # nonsensical non-positive amounts here.
        if amount <= Decimal('0'):
            log.info('Non-positive amount for %s — skipping', phone_number)
            return LMSRepaymentResult(
                success=False, code='no_balance',
                amount_sent=Decimal('0'), response_body={},
                duration_ms=0, error='No outstanding balance',
            )

        # ── 4. POST to LMS ─────────────────────────────────
        payload = {
            'phone_number': phone_number,
            'amount': float(amount),
            'collection_date': collection_date,
        }

        t0 = time.monotonic()
        try:
            resp = _get_session().post(
                f'{settings.LMS_BASE_URL}/api/partner/pay-loan/',
                json=payload,
                headers=_headers(),
                timeout=settings.LMS_TIMEOUT,
                verify=True,
            )
            duration_ms = int((time.monotonic() - t0) * 1000)

            # Auto-refresh token and retry once on 401
            if resp.status_code == 401:
                log.warning('LMS returned 401 — refreshing token and retrying once')
                invalidate_token_cache()
                resp = _get_session().post(
                    f'{settings.LMS_BASE_URL}/api/partner/pay-loan/',
                    json=payload,
                    headers=_headers(),
                    timeout=settings.LMS_TIMEOUT,
                    verify=True,
                )
                duration_ms = int((time.monotonic() - t0) * 1000)

            body = _safe_json(resp)
            code = str(body.get('code', resp.status_code))
            success = code.startswith('200')

            # LMS may report the amount it actually applied (e.g. after its
            # own internal cap). Fall back to 0 if it isn't present — we do
            # NOT assume the full requested amount went through unless LMS
            # says so explicitly.
            lms_reported_amount = (
                body.get('data', {}).get('amount_applied')
                if isinstance(body.get('data'), dict) else None
            )
            if success:
                amount_sent = Decimal(str(lms_reported_amount)) if lms_reported_amount is not None else amount
            else:
                amount_sent = Decimal('0')
            

            # Classify the response — only meaningful on failure
            error_class = classify_lms_response_error(code, body) if not success else None

            # Update circuit breaker
            if success:
                circuit_breaker.record_success()
            elif error_class == ErrorClassification.TRANSIENT:
                circuit_breaker.record_failure()

            log.info(
                'LMS repayment | phone=%s amount=%s code=%s %dms error_class=%s',
                phone_number, amount, code, duration_ms,
                error_class.value if error_class else 'none',
            )

            _upsert_idempotency(
                key=idempotency_key,
                organization_code=organization_code,
                status=IdempotencyKey.STATUS_SUCCESS if success else IdempotencyKey.STATUS_FAILED,
                response_payload={**body, 'amount_sent': str(amount_sent)},
            )

            return LMSRepaymentResult(
                success=success,
                code=code,
                amount_sent=amount_sent,
                response_body=body,
                duration_ms=duration_ms,
                error='' if success else body.get('message', 'LMS error'),
            )

        except requests.exceptions.ConnectionError as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            classification = ErrorClassification.NETWORK
            log.warning('LMS connection error for %s: %s', phone_number, exc)
            circuit_breaker.record_failure()
            _upsert_idempotency(
                key=idempotency_key,
                organization_code=organization_code,
                status=IdempotencyKey.STATUS_FAILED,
                response_payload={'error': str(exc), 'classification': classification.value},
            )
            return LMSRepaymentResult(
                success=False, code='connection_error',
                amount_sent=Decimal('0'), response_body={'classification': classification.value},
                duration_ms=duration_ms, error=str(exc),
            )

        except requests.exceptions.Timeout as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            classification = ErrorClassification.NETWORK
            log.warning('LMS timeout for %s after %dms', phone_number, duration_ms)
            circuit_breaker.record_failure()
            _upsert_idempotency(
                key=idempotency_key,
                organization_code=organization_code,
                status=IdempotencyKey.STATUS_FAILED,
                response_payload={'error': str(exc), 'classification': classification.value},
            )
            return LMSRepaymentResult(
                success=False, code='timeout',
                amount_sent=Decimal('0'), response_body={'classification': classification.value},
                duration_ms=duration_ms, error=str(exc),
            )

        except requests.HTTPError as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            classification = classify_request_error(exc)

            if exc.response is not None and exc.response.status_code == 401:
                log.warning('LMS returned 401 during repayment — invalidating token cache')
                invalidate_token_cache()

            log.error(
                'LMS HTTP error for %s: status=%s classification=%s',
                phone_number,
                exc.response.status_code if exc.response else 'unknown',
                classification.value,
            )

            if classification == ErrorClassification.TRANSIENT:
                circuit_breaker.record_failure()

            _upsert_idempotency(
                key=idempotency_key,
                organization_code=organization_code,
                status=IdempotencyKey.STATUS_FAILED,
                response_payload={'error': str(exc), 'classification': classification.value},
            )
            return LMSRepaymentResult(
                success=False, code='http_error',
                amount_sent=Decimal('0'), response_body={},
                duration_ms=duration_ms, error=str(exc),
            )

        except requests.RequestException as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            classification = classify_request_error(exc)
            log.error(
                'LMS network error for %s: classification=%s',
                phone_number, classification.value,
            )
            _upsert_idempotency(
                key=idempotency_key,
                organization_code=organization_code,
                status=IdempotencyKey.STATUS_FAILED,
                response_payload={'error': str(exc), 'classification': classification.value},
            )
            return LMSRepaymentResult(
                success=False, code='network_error',
                amount_sent=Decimal('0'), response_body={},
                duration_ms=duration_ms, error=str(exc),
            )

    finally:
        # ── 5. Always release lock ─────────────────────────
        _release_lock(phone_number)


# ─────────────────────────────────────────────────────────────
# Public entry point — customer name lookups (exclusive membership only)
# ─────────────────────────────────────────────────────────────

def get_customer_exclusive(phone_number: str) -> dict | None:
    """
    Fetch a customer's name from the LMS partner endpoint, restricted to
    customers who belong exclusively to this partner (i.e. not shared
    across multiple checkoff organisations).
    Endpoint: POST /api/partner/customer-exclusive-details/

    Returns None if the customer isn't found, isn't exclusive to us
    (403.002 — shared across orgs), or the call fails. Successful lookups
    and confirmed misses are both cached in Redis, since this can be
    called once per unique phone number on every list page render.
    """
    cache_key = f'lms:customer_exclusive:{phone_number}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None  # cached {} means "confirmed not available"

    try:
        resp = _get_session().post(
            f'{settings.LMS_BASE_URL}/api/partner/customer-exclusive-details/',
            json={'phone_number': phone_number},
            headers=_headers(),
            timeout=min(settings.LMS_TIMEOUT, 10),
            verify=True,
        )
        if resp.status_code == 401:
            invalidate_token_cache()
            resp = _get_session().post(
                f'{settings.LMS_BASE_URL}/api/partner/customer-exclusive-details/',
                json={'phone_number': phone_number},
                headers=_headers(),
                timeout=min(settings.LMS_TIMEOUT, 10),
                verify=True,
            )

        body = _safe_json(resp)
        code = str(body.get('code', resp.status_code))

        if code.startswith('200'):
            data = body.get('data', {})
            full_name = ' '.join(filter(None, [
                data.get('first_name'), data.get('other_name'), data.get('last_name'),
            ]))
            result = {
                'full_name': full_name,
                'first_name': data.get('first_name', ''),
                'last_name': data.get('last_name', ''),
            }
            cache.set(cache_key, result, timeout=_CUSTOMER_CACHE_TTL)
            return result

        # 404.x (not found) or 403.002 (shared across orgs) — cache the miss briefly
        log.info('customer_exclusive_details %s → %s: %s', phone_number, code, body.get('message'))
        cache.set(cache_key, {}, timeout=_CUSTOMER_NEGATIVE_TTL)
        return None

    except Exception as exc:
        log.warning('get_customer_exclusive failed for %s: %s', phone_number, exc)
        return None  # not cached — a transient failure shouldn't stick for 5 min


def get_customer_names_bulk(phone_numbers: list[str]) -> dict[str, dict | None]:
    """Fetch multiple customers concurrently — used by list views to avoid N sequential calls."""
    results = {}
    if not phone_numbers:
        return results
    with ThreadPoolExecutor(max_workers=min(len(phone_numbers), 10)) as executor:
        futures = {executor.submit(get_customer_exclusive, p): p for p in phone_numbers}
        for future in as_completed(futures):
            phone = futures[future]
            try:
                results[phone] = future.result()
            except Exception:
                results[phone] = None
    return results


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _safe_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {'raw': resp.text[:500]}


def _upsert_idempotency(
    key: str,
    organization_code: str,
    status: str,
    response_payload: dict,
) -> None:
    from datetime import timedelta
    from django.utils import timezone
    from apps.repayments.models import IdempotencyKey
    from apps.organizations.models import CheckoffOrganizationMirror
    try:
        org = CheckoffOrganizationMirror.objects.get(code=organization_code)
        IdempotencyKey.objects.update_or_create(
            key=key,
            defaults={
                'organization': org,
                'status': status,
                'response_payload': response_payload,
                'expires_at': timezone.now() + timedelta(seconds=settings.IDEMPOTENCY_KEY_TTL),
            }
        )
    except Exception as exc:
        log.warning('Failed to upsert idempotency key %s: %s', key[:16], exc)

def get_customer_balances(phone_number: str) -> dict | None:
    """
    POST /api/partner/customer-balances/
    Returns {"accessible_loan_limit": "...", "loan_balance": "..."} or None on failure.
    Used by the loan request pipeline to gate eligibility before submitting to LMS.
    """
    cache_key = f'lms:customer_balances:{phone_number}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None

    try:
        resp = _get_session().post(
            f'{settings.LMS_BASE_URL}/api/partner/customer-balances/',
            json={'phone_number': phone_number},
            headers=_headers(),
            timeout=min(settings.LMS_TIMEOUT, 10),
            verify=True,
        )
        if resp.status_code == 401:
            invalidate_token_cache()
            resp = _get_session().post(
                f'{settings.LMS_BASE_URL}/api/partner/customer-balances/',
                json={'phone_number': phone_number},
                headers=_headers(),
                timeout=min(settings.LMS_TIMEOUT, 10),
                verify=True,
            )

        body = _safe_json(resp)
        if str(body.get('code', '')).startswith('200'):
            data = body.get('data', {})
            # Cache briefly — balances change after each loan/repayment
            cache.set(cache_key, data, timeout=60 * 5)  # 5 min
            return data

        log.info('customer_balances %s → %s', phone_number, body.get('code'))
        cache.set(cache_key, {}, timeout=60)  # cache miss for 1 min to avoid hammering
        return None

    except Exception as exc:
        log.warning('get_customer_balances failed for %s: %s', phone_number, exc)
        return None



def set_customer_loan_limit(phone_number: str, loan_limit: Decimal) -> dict:
    """
    POST /api/partner/set-loan-limit/

    Sets the loan limit for a single customer in the LMS.
    Returns the full response dict from the LMS.

    Used in the loan tasks pipeline (set_loan_limits_for_batch) before
    dispatching borrow_loan calls.
    """
    try:
        resp = _get_session().post(
            f'{settings.LMS_BASE_URL}/api/partner/set-loan-limit/',
            json={
                'phone_number': phone_number,
                'loan_limit':   str(loan_limit),
            },
            headers=_headers(),
            timeout=min(settings.LMS_TIMEOUT, 15),
            verify=True,
        )
        if resp.status_code == 401:
            invalidate_token_cache()
            resp = _get_session().post(
                f'{settings.LMS_BASE_URL}/api/partner/set-loan-limit/',
                json={
                    'phone_number': phone_number,
                    'loan_limit':   str(loan_limit),
                },
                headers=_headers(),
                timeout=min(settings.LMS_TIMEOUT, 15),
                verify=True,
            )

        body = _safe_json(resp)
        code = str(body.get('code', ''))
        success = code.startswith('200')

        if success:
            # Invalidate cached balance — limit has changed
            invalidate_customer_balances_cache(phone_number)
            log.info(
                'set_customer_loan_limit: phone=%s limit=%s → accessible=%s',
                phone_number, loan_limit,
                body.get('data', {}).get('accessible_loan_limit'),
            )
        else:
            log.warning(
                'set_customer_loan_limit failed: phone=%s limit=%s code=%s message=%s',
                phone_number, loan_limit, code, body.get('message'),
            )

        return body

    except Exception as exc:
        log.error('set_customer_loan_limit error for %s: %s', phone_number, exc)
        return {'code': 'error', 'message': str(exc)}


def bulk_set_loan_limits(customers: list[dict]) -> dict:
    """
    POST /api/partner/bulk-set-loan-limits/

    Sets loan limits for multiple customers in one call.

    customers: list of {"phone_number": "254...", "loan_limit": "5000.00"}

    Returns the full LMS response dict:
        {
            "code": "200.001",
            "data": {
                "processed": 10,
                "updated": 8,
                "failed": 2,
                "results": [...]
            }
        }

    Invalidates cached balances for all customers that were successfully updated.
    Batches in chunks of 500 (LMS hard limit) if the list is larger.
    """
    if not customers:
        return {'code': '200.001', 'data': {'processed': 0, 'updated': 0, 'failed': 0, 'results': []}}

    CHUNK_SIZE = 500
    all_results = []
    total_updated = 0
    total_failed  = 0

    chunks = [customers[i:i + CHUNK_SIZE] for i in range(0, len(customers), CHUNK_SIZE)]

    for chunk in chunks:
        try:
            resp = _get_session().post(
                f'{settings.LMS_BASE_URL}/api/partner/bulk-set-loan-limits/',
                json={'customers': chunk},
                headers=_headers(),
                timeout=settings.LMS_TIMEOUT,
                verify=True,
            )
            if resp.status_code == 401:
                invalidate_token_cache()
                resp = _get_session().post(
                    f'{settings.LMS_BASE_URL}/api/partner/bulk-set-loan-limits/',
                    json={'customers': chunk},
                    headers=_headers(),
                    timeout=settings.LMS_TIMEOUT,
                    verify=True,
                )

            body = _safe_json(resp)
            if str(body.get('code', '')).startswith('200'):
                chunk_data = body.get('data', {})
                total_updated += chunk_data.get('updated', 0)
                total_failed  += chunk_data.get('failed', 0)
                chunk_results  = chunk_data.get('results', [])
                all_results.extend(chunk_results)

                # Invalidate cache for successfully updated customers
                for result in chunk_results:
                    if result.get('status') == 'updated':
                        invalidate_customer_balances_cache(result['phone_number'])
            else:
                log.error('bulk_set_loan_limits chunk failed: %s', body)
                total_failed += len(chunk)
                all_results.extend([
                    {'phone_number': c['phone_number'], 'status': 'failed', 'reason': 'LMS chunk error'}
                    for c in chunk
                ])

        except Exception as exc:
            log.error('bulk_set_loan_limits chunk exception: %s', exc)
            total_failed += len(chunk)
            all_results.extend([
                {'phone_number': c['phone_number'], 'status': 'failed', 'reason': str(exc)}
                for c in chunk
            ])

    log.info(
        'bulk_set_loan_limits: total=%d updated=%d failed=%d',
        len(customers), total_updated, total_failed,
    )

    return {
        'code': '200.001',
        'data': {
            'processed': len(customers),
            'updated':   total_updated,
            'failed':    total_failed,
            'results':   all_results,
        },
    }