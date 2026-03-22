"""
Singleton HTTP client for the Dime LMS (back.dimeapp.co.ke).

Guarantees:
  - Shared connection pool across worker threads (lru_cache Session)
  - Per-phone Redis mutex → no concurrent duplicate repayments
  - Amount capped at loan balance → no negative balances
  - Idempotency table check → no re-send on retry
  - Exponential backoff retries via urllib3 Retry
  - OAuth2 consumer key/secret token auth with auto-refresh on 401
"""
import logging
import time
from decimal import Decimal
from functools import lru_cache

import requests
from django.conf import settings
from django.core.cache import cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

_LOCK_TTL = 60  # seconds


# ─────────────────────────────────────────────────────────────
# Connection pool (one Session per worker process)
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_session() -> requests.Session:
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
            resp = _get_session().post(
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
                or data.get('access')
                or data.get('access_token')
                or data.get('token')
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
            expires_at = nested.get('expires_at') or data.get('expires_at')
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
                    log.warning('Could not parse expires_at=%s: %s — using default TTL', expires_at, ttl_exc)
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
# Public entry point
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
    Thread-safe repayment dispatch.

    Steps:
      1. Acquire per-phone Redis mutex (prevents race conditions)
      2. Idempotency check (prevents duplicate sends on retry)
      3. Cap amount at current loan balance (prevents negative balances)
      4. POST to LMS /api/main/register-repayment/
      5. Release mutex in finally block regardless of outcome
    """
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

        # ── 3. Cap amount at loan balance ──────────────────
        capped = _cap_amount_to_balance(phone_number, amount)
        if capped <= Decimal('0'):
            log.info('No outstanding balance for %s — skipping', phone_number)
            return LMSRepaymentResult(
                success=False, code='no_balance',
                amount_sent=Decimal('0'), response_body={},
                duration_ms=0, error='No outstanding balance',
            )

        # ── 4. POST to LMS ─────────────────────────────────
        payload = {
            'phone_number': phone_number,
            'amount': float(capped),
            'collection_date': collection_date,
        }

        t0 = time.monotonic()
        try:
            resp = _get_session().post(
                f'{settings.LMS_BASE_URL}/api/main/register-repayment/',
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
                    f'{settings.LMS_BASE_URL}/api/main/register-repayment/',
                    json=payload,
                    headers=_headers(),
                    timeout=settings.LMS_TIMEOUT,
                    verify=True,
                )
                duration_ms = int((time.monotonic() - t0) * 1000)

            body = _safe_json(resp)
            code = str(body.get('code', resp.status_code))
            success = code.startswith('200')

            log.info(
                'LMS repayment | phone=%s amount=%s code=%s %dms',
                phone_number, capped, code, duration_ms,
            )

            _upsert_idempotency(
                key=idempotency_key,
                organization_code=organization_code,
                status=IdempotencyKey.STATUS_SUCCESS if success else IdempotencyKey.STATUS_FAILED,
                response_payload={**body, 'amount_sent': str(capped)},
            )

            return LMSRepaymentResult(
                success=success,
                code=code,
                amount_sent=capped if success else Decimal('0'),
                response_body=body,
                duration_ms=duration_ms,
                error='' if success else body.get('message', 'LMS error'),
            )

        except requests.HTTPError as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            if exc.response is not None and exc.response.status_code == 401:
                log.warning('LMS returned 401 during repayment — invalidating token cache')
                invalidate_token_cache()
            log.error('LMS HTTP error for %s: %s', phone_number, exc)
            _upsert_idempotency(
                key=idempotency_key,
                organization_code=organization_code,
                status=IdempotencyKey.STATUS_FAILED,
                response_payload={'error': str(exc)},
            )
            return LMSRepaymentResult(
                success=False, code='http_error',
                amount_sent=Decimal('0'), response_body={},
                duration_ms=duration_ms, error=str(exc),
            )

        except requests.RequestException as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            log.error('LMS network error for %s: %s', phone_number, exc)
            _upsert_idempotency(
                key=idempotency_key,
                organization_code=organization_code,
                status=IdempotencyKey.STATUS_FAILED,
                response_payload={'error': str(exc)},
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
# Helpers
# ─────────────────────────────────────────────────────────────

def _cap_amount_to_balance(phone_number: str, requested: Decimal) -> Decimal:
    """
    Fetch active loan balance from LMS and return min(requested, balance).
    Returns Decimal('0') if the borrower has no active loans.
    Falls back to the requested amount if the LMS call fails — the LMS
    will reject any genuine overpayment on its side.
    """
    try:
        resp = _get_session().post(
            f'{settings.LMS_BASE_URL}/api/main/get-borrower-loans/',
            json={'phone_number': phone_number, 'page': 1, 'count': 10},
            headers=_headers(),
            timeout=settings.LMS_TIMEOUT,
            verify=True,
        )
        if resp.status_code == 401:
            invalidate_token_cache()
            resp = _get_session().post(
                f'{settings.LMS_BASE_URL}/api/main/get-borrower-loans/',
                json={'phone_number': phone_number, 'page': 1, 'count': 10},
                headers=_headers(),
                timeout=settings.LMS_TIMEOUT,
                verify=True,
            )
        loans = _safe_json(resp).get('data', [])
        active = [l for l in loans if l.get('loan_status_id') == 1]
        if not active:
            return Decimal('0')
        total = sum(Decimal(str(l.get('balance_amount', 0))) for l in active)
        return min(requested, total)
    except Exception as exc:
        log.warning(
            'Could not fetch balance for %s: %s — using requested amount as fallback',
            phone_number, exc,
        )
        return requested


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