# Production-Grade Repayment Engine

## Overview

The Dime HR Payroll repayment system has been upgraded with production-grade error handling, circuit breaker pattern, and intelligent retry logic to survive transient network failures without causing retry storms.

---

## Architecture Improvements

### 1. Error Classification

All errors are now categorized into four types:

| Type | Examples | Retry Strategy |
|------|----------|-----------------|
| **Network** | DNS failure, connection timeout, connection refused | Aggressive exponential backoff (30s, 60s, 120s) |
| **Transient** | 5xx errors, 429 rate limit | Slower exponential backoff (60s, 120s, 300s) |
| **Business** | 4xx validation, insufficient balance | **No retry** |
| **Auth** | 401, 403 invalid credentials | Refresh token, single retry, then fail |

### 2. Circuit Breaker Pattern

Prevents cascading failures when the LMS is temporarily unavailable:

```
CLOSED (normal) 
  ↓ [5 consecutive failures]
OPEN (reject requests)
  ↓ [after 5 minutes]
HALF_OPEN (test 1 request)
  ↓ [success? → CLOSED | failure → OPEN]
```

**Benefits:**
- Stop wasting resources retrying a down service
- Return fast errors instead of hanging
- Automatic recovery after timeout

### 3. Intelligent Retry Logic

The task `dispatch_single_repayment` now:

1. **Classifies each error** from the LMS response
2. **Extracts retry countdown** based on classification
3. **Respects max_retries** limit
4. **Logs classification** for observability

Example:
```python
# DNS failure (network error) → retry after 30s
# HTTP 503 (transient) → retry after 60s  
# HTTP 400 (business) → don't retry, mark failed
# Circuit open → retry after 120s
```

---

## Code Changes

### New Module: `apps/api/error_handling.py`

Provides:
- `ErrorClassification` enum
- `LMSCircuitBreaker` class
- Classification functions
- Custom exception types

### Updated: `apps/api/lms_client.py`

Changes:
- ✅ Circuit breaker check at start of `send_repayment()`
- ✅ Error classification on all request exceptions
- ✅ Circuit breaker updates on transient failures
- ✅ Structured logging with error classification
- ✅ Separate exception handlers for each error type

### Updated: `apps/repayments/tasks.py`

Changes:
- ✅ Intelligent retry countdown calculation
- ✅ Error classification extraction from response
- ✅ Circuit breaker handling
- ✅ Better logging for observability

---

## Deployment Checklist

### Before deploying to production:

- [ ] Ensure Redis is running (circuit breaker uses cache)
- [ ] Update settings to configure retry timeouts:
  ```python
  LMS_RETRY_MAX = 3
  LMS_RETRY_BACKOFF = 2
  LMS_TIMEOUT = 60
  ```
- [ ] Optional: Pin Docker DNS to avoid transient resolution failures:
  ```json
  {
    "dns": ["1.1.1.1", "8.8.8.8"]
  }
  ```

### After deploying:

1. Test with a known-good repayment:
   ```bash
   python manage.py shell
   >>> from apps.repayments.tasks import dispatch_single_repayment
   >>> dispatch_single_repayment.delay(deduction_id, batch_id)
   ```

2. Monitor logs for error classifications:
   ```bash
   docker logs <celery_worker_container> | grep "classification="
   ```

3. Verify circuit breaker is working (simulate LMS down):
   ```bash
   docker logs <celery_worker_container> | grep "Circuit breaker"
   ```

---

## Observability

### New Log Entries

```
# Successful repayment
LMS repayment | phone=+254712345678 amount=1000 code=200.001 100ms error_class=none

# Network error (will retry)
LMS network error for +254712345678: classification=network

# Business error (won't retry)
LMS HTTP error for +254712345678: status=400 classification=business

# Circuit breaker open
Circuit breaker OPEN — rejecting repayment for +254712345678 (LMS temporarily unavailable)

# Intelligent retry decision
Retrying deduction <id> in 60s (attempt 2/3) classification=transient
```

### Metrics to Monitor

- `LMS_repayment_duration_ms` — request latency
- `Circuit_breaker_state` — CLOSED | OPEN | HALF_OPEN
- `Error_classification_counts` — network vs business vs transient
- `Retry_counts_by_classification` — which errors are retrying
- `Deduction_final_status` — SUCCESS | FAILED | SKIPPED | PROCESSING

---

## Troubleshooting

### Symptom: "Circuit breaker OPEN" messages

**Diagnosis:** LMS endpoint is down or unreachable

**Fix:**
1. Check LMS service health
2. Check network connectivity to LMS
3. Check DNS resolution:
   ```bash
   docker exec <web_container> nslookup back.dimeapp.co.ke
   ```
4. Wait 5 minutes for circuit to recover, or restart celery:
   ```bash
   docker restart <celery_worker_container>
   ```

### Symptom: Excessive retries for same deduction

**Diagnosis:** Error classification is incorrect or DNS is flaky

**Fix:**
1. Check logs for `classification=` values
2. If many `network` → Docker DNS is unstable (update DNS config)
3. If many `transient` → LMS has availability issues

### Symptom: Deductions stuck in PROCESSING

**Diagnosis:** Worker crashed or retry exceeded max_retries

**Fix:**
1. Check worker logs
2. Manually reset status:
   ```bash
   python manage.py shell
   >>> from apps.payroll.models import SalaryDeduction
   >>> d = SalaryDeduction.objects.get(id=deduction_id)
   >>> d.status = SalaryDeduction.STATUS_QUEUED
   >>> d.save()
   ```
3. Restart dispatch:
   ```bash
   >>> from apps.repayments.tasks import dispatch_single_repayment
   >>> dispatch_single_repayment.delay(d.id, batch_id)
   ```

---

## Configuration

### Optional: Docker DNS (recommended for production)

Create/update `/etc/docker/daemon.json`:

```json
{
  "dns": ["1.1.1.1", "8.8.8.8"]
}
```

Then restart Docker:
```bash
sudo systemctl restart docker
docker-compose -f docker-compose.prod.yml down
docker-compose -f docker-compose.prod.yml up -d
```

**Why:** Removes dependency on systemd-resolved, prevents transient DNS failures.

---

## Future Enhancements

1. **Cloud Storage Integration** (S3/Azure Blob)
   - Shared file storage between web + worker containers
   - No more volume mount issues

2. **Webhook Callbacks**
   - LMS notifies us of repayment confirmations
   - Real-time status updates instead of polling

3. **Rate Limiting per Organization**
   - Prevent spam uploads
   - Fair-share resource allocation

4. **Batch-level Circuit Breaker**
   - Pause entire batch if LMS is down
   - Resume automatically on recovery

5. **Metrics Export**
   - Prometheus-compatible metrics endpoint
   - Grafana dashboards for monitoring

---

## Summary

| Improvement | Before | After |
|------------|--------|-------|
| DNS error handling | Retry storm | Fast failure, backoff |
| Network vs business errors | Same retry logic | Different strategies |
| LMS downtime | Cascading failures | Circuit breaker stops retries |
| Observability | Generic "error" | Classified errors with reasons |
| Recovery | Manual restart | Automatic circuit recovery |

This upgrade makes the repayment engine **production-ready** and resilient to transient failures.
