import logging
from decimal import Decimal, InvalidOperation

import openpyxl
from celery import shared_task, chord, group
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.api.error_handling import ErrorClassification, get_retry_countdown

log = logging.getLogger(__name__)

MAX_RETRIES = 3


@shared_task
def finalize_eligibility(results, upload_id: str):
    """
    Chord callback after all eligibility checks complete.
    Tallies eligible/ineligible counts, creates LoanRequestBatch in DRAFT status.
    Admin must then approve the batch (approve_and_dispatch action) which fires
    set_loan_limits_for_batch → dispatch_loan_batch.
    """
    from apps.loans.models import LoanRequestUpload, LoanRequest, LoanRequestBatch
    from django.db.models import Sum

    try:
        upload = LoanRequestUpload.objects.get(id=upload_id)

        eligible_qs   = LoanRequest.objects.filter(upload=upload, status=LoanRequest.STATUS_ELIGIBLE)
        ineligible_qs = LoanRequest.objects.filter(upload=upload, status=LoanRequest.STATUS_INELIGIBLE)

        upload.eligible_rows   = eligible_qs.count()
        upload.ineligible_rows = ineligible_qs.count()
        upload.save(update_fields=['eligible_rows', 'ineligible_rows'])

        if not eligible_qs.exists():
            log.warning(
                'finalize_eligibility: no eligible requests for upload %s — batch not created',
                upload_id,
            )
            return

        total_amount = eligible_qs.aggregate(t=Sum('requested_amount'))['t'] or Decimal('0.00')

        batch, created = LoanRequestBatch.objects.get_or_create(
            upload=upload,
            defaults={
                'organization':   upload.organization,
                'total_requests': eligible_qs.count(),
                'total_amount':   total_amount,
                'status':         LoanRequestBatch.STATUS_DRAFT,
            },
        )
        if not created:
            batch.total_requests = eligible_qs.count()
            batch.total_amount   = total_amount
            batch.save(update_fields=['total_requests', 'total_amount'])

        log.info(
            'finalize_eligibility: upload=%s eligible=%d ineligible=%d '
            'batch=%s status=DRAFT — awaiting admin approval to dispatch',
            upload_id, upload.eligible_rows, upload.ineligible_rows, batch.id,
        )

    except Exception as exc:
        log.exception('finalize_eligibility error for upload %s: %s', upload_id, exc)


@shared_task(bind=True)
def set_loan_limits_for_batch(self, batch_id: str):
    """
    Step triggered by admin approve_and_dispatch action (before dispatch_loan_batch).

    1. Collects all eligible LoanRequest rows for this batch.
    2. Calls LMS bulk-set-loan-limits with phone_number + requested_amount as the new limit.
    3. Marks LoanRequests whose limit-set failed as STATUS_FAILED with reason.
    4. Fires dispatch_loan_batch for all remaining eligible rows.

    Why requested_amount as the limit?
    The HR upload specifies how much each employee is allowed to borrow this cycle.
    That amount becomes their loan limit for this batch. The LMS then enforces
    that ceiling when borrow_loan is called.
    """
    from apps.loans.models import LoanRequestBatch, LoanRequest
    from apps.api.lms_client import bulk_set_loan_limits

    try:
        batch = LoanRequestBatch.objects.select_related('organization').get(id=batch_id)
    except LoanRequestBatch.DoesNotExist:
        log.error('set_loan_limits_for_batch: batch %s not found', batch_id)
        return

    eligible_requests = list(
        LoanRequest.objects.filter(
            upload=batch.upload,
            status=LoanRequest.STATUS_ELIGIBLE,
        ).select_related('organization')
    )

    if not eligible_requests:
        log.warning('set_loan_limits_for_batch: no eligible requests for batch %s', batch_id)
        dispatch_loan_batch.delay(batch_id)
        return

    # Build the payload for the bulk endpoint
    customers_payload = [
        {
            'phone_number': req.phone_number,
            'loan_limit':   str(
                (req.existing_loan_balance or Decimal('0.00')) + req.requested_amount
            ),
        }
        for req in eligible_requests
    ]

    log.info(
        'set_loan_limits_for_batch: setting limits for %d customers (batch=%s). '
        'Limits = existing_balance + requested_amount.',
        len(customers_payload), batch_id,
    )

    result = bulk_set_loan_limits(customers_payload)
    result_data = result.get('data', {})
    results_list = result_data.get('results', [])

    # Build a lookup: phone_number → result status
    # Note: if multiple rows share a phone (unlikely but possible), last result wins.
    result_by_phone = {r['phone_number']: r for r in results_list}

    failed_phones = set()
    for req in eligible_requests:
        phone_result = result_by_phone.get(req.phone_number)
        if phone_result and phone_result.get('status') != 'updated':
            # Limit-set failed — mark the request as failed so it won't be dispatched
            req.status         = LoanRequest.STATUS_FAILED
            req.failure_reason = f"Loan limit update failed: {phone_result.get('reason', 'LMS error')}"
            req.save(update_fields=['status', 'failure_reason'])
            failed_phones.add(req.phone_number)
            log.warning(
                'set_loan_limits_for_batch: limit set FAILED for %s — reason: %s',
                req.phone_number, phone_result.get('reason'),
            )

    log.info(
        'set_loan_limits_for_batch: batch=%s limits_set=%d limit_failed=%d — proceeding to dispatch',
        batch_id,
        result_data.get('updated', 0),
        result_data.get('failed', 0),
    )

    # Always proceed to dispatch — dispatch_loan_batch will skip non-eligible rows
    dispatch_loan_batch.delay(batch_id)


@shared_task(bind=True)
def dispatch_loan_batch(self, batch_id: str):
    """
    Fan-out chord — one dispatch_single_loan task per eligible LoanRequest.
    Only rows still in STATUS_ELIGIBLE at this point are dispatched.
    Rows marked FAILED by set_loan_limits_for_batch are automatically skipped.
    """
    from apps.loans.models import LoanRequestBatch, LoanRequest

    try:
        batch = LoanRequestBatch.objects.select_related('organization').get(id=batch_id)
    except LoanRequestBatch.DoesNotExist:
        log.error('dispatch_loan_batch: batch %s not found', batch_id)
        return

    if batch.status not in (LoanRequestBatch.STATUS_APPROVED, LoanRequestBatch.STATUS_DRAFT):
        log.warning('dispatch_loan_batch: batch %s is %s — skipping', batch_id, batch.status)
        return

    batch.status = LoanRequestBatch.STATUS_DISPATCHING
    batch.save(update_fields=['status'])

    eligible_ids = list(
        LoanRequest.objects.filter(
            upload=batch.upload,
            status=LoanRequest.STATUS_ELIGIBLE,   # only rows whose limit-set succeeded
        ).values_list('id', flat=True)
    )

    if not eligible_ids:
        batch.status = LoanRequestBatch.STATUS_COMPLETE
        batch.save(update_fields=['status'])
        log.info('dispatch_loan_batch: no eligible requests remaining for batch %s', batch_id)
        return

    job = chord(
        group(dispatch_single_loan.s(str(r_id), batch_id) for r_id in eligible_ids),
        finalize_loan_batch.s(batch_id),
    )
    job.apply_async()
    log.info('dispatch_loan_batch: dispatched %d tasks for batch %s', len(eligible_ids), batch_id)


@shared_task(bind=True, max_retries=MAX_RETRIES, acks_late=True, reject_on_worker_lost=True)
def dispatch_single_loan(self, request_id: str, batch_id: str):
    from apps.loans.models import LoanRequest, LoanRequestBatch
    from apps.api.lms_client import send_loan_request

    try:
        req = LoanRequest.objects.select_related('organization').get(id=request_id)
    except LoanRequest.DoesNotExist:
        log.error('dispatch_single_loan: request %s not found', request_id)
        return 'not_found'

    try:
        LoanRequestBatch.objects.get(id=batch_id)
    except LoanRequestBatch.DoesNotExist:
        return 'batch_not_found'

    req.status          = LoanRequest.STATUS_PROCESSING
    req.last_attempt_at = timezone.now()
    req.attempts       += 1
    req.save(update_fields=['status', 'last_attempt_at', 'attempts'])

    result = send_loan_request(
        phone_number    = req.phone_number,
        amount          = float(req.requested_amount),
        reference       = req.reference or str(req.idempotency_key)[:50],
        idempotency_key = req.idempotency_key,
    )

    if result['success']:
        req.status      = LoanRequest.STATUS_SUCCESS
        req.lms_loan_id = result['loan_id']
    elif result.get('code') in ('no_limit', 'circuit_open'):
        req.status = LoanRequest.STATUS_SKIPPED
    else:
        req.status = LoanRequest.STATUS_FAILED

    req.lms_response   = result['body']
    req.failure_reason = result['error'] if not result['success'] else ''
    req.save(update_fields=['status', 'lms_loan_id', 'lms_response', 'failure_reason'])

    if (
        not result['success']
        and result.get('code') not in ('no_limit', 'circuit_open')
        and self.request.retries < MAX_RETRIES
    ):
        classification = result.get('classification')
        if classification:
            countdown = get_retry_countdown(self.request.retries + 1, classification)
            if countdown > 0:
                log.info(
                    'Retrying loan request %s in %ds (attempt %d/%d, classification=%s)',
                    request_id, countdown, self.request.retries + 1, MAX_RETRIES,
                    classification.value,
                )
                raise self.retry(countdown=countdown)

    return req.status


@shared_task
def finalize_loan_batch(results, batch_id: str):
    from apps.loans.models import LoanRequestBatch
    try:
        batch = LoanRequestBatch.objects.get(id=batch_id)
        batch.refresh_counters()
        batch.status = (
            LoanRequestBatch.STATUS_FAILED
            if batch.failed_count == batch.total_requests
            else LoanRequestBatch.STATUS_COMPLETE
        )
        batch.save(update_fields=['status'])
        log.info(
            'finalize_loan_batch: %s → %s (ok=%d fail=%d skip=%d)',
            batch_id, batch.status,
            batch.successful_count, batch.failed_count, batch.skipped_count,
        )
    except Exception as exc:
        log.exception('finalize_loan_batch error for batch %s: %s', batch_id, exc)