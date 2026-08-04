import logging
from decimal import Decimal, InvalidOperation

import openpyxl
from celery import shared_task, chord, group
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.api.error_handling import ErrorClassification, get_retry_countdown

log = logging.getLogger(__name__)

MAX_RETRIES = 3

COLUMN_ALIASES = {
    'phone_number':  ['phone_number', 'phone', 'msisdn', 'mobile', 'telephone'],
    'amount':        ['amount', 'loan_amount', 'requested_amount', 'request_amount'],
    'employee_name': ['employee_name', 'name', 'full_name', 'employee'],
    'employee_id':   ['employee_id', 'staff_id', 'payroll_number', 'emp_id'],
    'reference':     ['reference', 'ref', 'note'],
}


def _normalize_headers(headers):
    mapping = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for i, h in enumerate(headers):
            if str(h).strip().lower() in aliases:
                mapping[canonical] = i
                break
    return mapping


def _parse_excel(file_field):
    file_field.seek(0)
    wb = openpyxl.load_workbook(file_field, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    headers = [str(h).strip() if h is not None else '' for h in rows[0]]
    col_map = _normalize_headers(headers)

    missing = [c for c in ('phone_number', 'amount') if c not in col_map]
    if missing:
        return [], [f'Missing required columns: {missing}. Found headers: {headers}']

    result = []
    for row in rows[1:]:
        if all(c is None for c in row):
            continue
        result.append({k: row[v] for k, v in col_map.items() if v < len(row)})
    return result, []


def _validate_row(row: dict, row_num: int) -> dict:
    from apps.api.validators import normalize_phone, validate_phone
    errors = []

    raw_phone = str(row.get('phone_number') or '').strip()
    phone = normalize_phone(raw_phone)
    if not phone or not validate_phone(phone):
        errors.append(f'Row {row_num}: invalid phone "{raw_phone}"')

    try:
        amount = Decimal(str(row.get('amount', '') or '0').replace(',', '').strip())
        if amount <= Decimal('0'):
            errors.append(f'Row {row_num}: amount must be > 0')
    except InvalidOperation:
        amount = Decimal('0')
        errors.append(f'Row {row_num}: invalid amount "{row.get("amount")}"')

    if errors:
        return {'error': True, 'messages': errors, 'row': row_num}

    return {
        'error':         False,
        'phone_number':  phone,
        'amount':        amount,
        'employee_name': str(row.get('employee_name') or '').strip()[:200],
        'employee_id':   str(row.get('employee_id') or '').strip()[:100],
        'reference':     str(row.get('reference') or '').strip()[:100],
    }


# ─────────────────────────────────────────────────────────────
# Step 1 — Parse upload (fires after admin approval)
# ─────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def parse_loan_upload(self, upload_id: str):
    from apps.loans.models import LoanRequestUpload, LoanRequest

    try:
        upload = LoanRequestUpload.objects.select_related('organization').get(id=upload_id)
    except LoanRequestUpload.DoesNotExist:
        log.error('parse_loan_upload: upload %s not found', upload_id)
        return

    if upload.status not in (
        LoanRequestUpload.STATUS_APPROVED,
        LoanRequestUpload.STATUS_PROCESSING,
    ):
        log.warning(
            'parse_loan_upload: upload %s is %s — skipping (must be approved first)',
            upload_id, upload.status,
        )
        return

    upload.status         = LoanRequestUpload.STATUS_PROCESSING
    upload.celery_task_id = self.request.id
    upload.save(update_fields=['status', 'celery_task_id'])

    errors       = []
    rows_created = 0

    try:
        rows, parse_errors = _parse_excel(upload.file)
        if parse_errors:
            upload.status    = LoanRequestUpload.STATUS_FAILED
            upload.error_log = parse_errors
            upload.save(update_fields=['status', 'error_log'])
            log.error('parse_loan_upload: header errors for %s: %s', upload_id, parse_errors)
            return

        upload.total_rows = len(rows)
        upload.save(update_fields=['total_rows'])

        bulk_requests = []
        for i, row in enumerate(rows, start=2):
            result = _validate_row(row, i)
            if result['error']:
                errors.append(result)
                continue
            req = LoanRequest(
                upload           = upload,
                organization     = upload.organization,
                phone_number     = result['phone_number'],
                employee_name    = result.get('employee_name', ''),
                employee_id      = result.get('employee_id', ''),
                requested_amount = result['amount'],
                reference        = result.get('reference', ''),
                row_number       = i,
                status           = LoanRequest.STATUS_QUEUED,
            )
            req.idempotency_key = req.build_idempotency_key()
            bulk_requests.append(req)

        with db_transaction.atomic():
            LoanRequest.objects.bulk_create(bulk_requests, ignore_conflicts=True)
            rows_created = len(bulk_requests)

        upload.processed_rows = rows_created
        upload.failed_rows    = len(errors)
        upload.error_log      = errors
        upload.status = (
            LoanRequestUpload.STATUS_DONE if not errors else LoanRequestUpload.STATUS_PARTIAL
        )
        upload.save(update_fields=['processed_rows', 'failed_rows', 'error_log', 'status'])
        log.info(
            'parse_loan_upload: upload=%s created=%d errors=%d',
            upload_id, rows_created, len(errors),
        )

        check_loan_eligibility_batch.delay(upload_id)

    except Exception as exc:
        log.exception('parse_loan_upload failed for %s: %s', upload_id, exc)
        upload.status    = LoanRequestUpload.STATUS_FAILED
        upload.error_log = [{'error': str(exc)}]
        upload.save(update_fields=['status', 'error_log'])
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────
# Step 2 — Eligibility fan-out
# ─────────────────────────────────────────────────────────────

@shared_task(bind=True)
def check_loan_eligibility_batch(self, upload_id: str):
    from apps.loans.models import LoanRequest

    request_ids = list(
        LoanRequest.objects.filter(
            upload_id=upload_id,
            status__in=[LoanRequest.STATUS_QUEUED, LoanRequest.STATUS_FAILED],
        ).values_list('id', flat=True)
    )

    if not request_ids:
        log.warning(
            'check_loan_eligibility_batch: no queued requests for upload %s', upload_id,
        )
        return

    job = chord(
        group(check_single_eligibility.s(str(r_id)) for r_id in request_ids),
        finalize_eligibility.s(upload_id),
    )
    job.apply_async()
    log.info(
        'check_loan_eligibility_batch: dispatched %d eligibility checks for upload %s',
        len(request_ids), upload_id,
    )


# ─────────────────────────────────────────────────────────────
# Step 3 — Single eligibility check
# Only gate: customer must exist and belong to this partner's org.
# We do NOT check accessible_loan_limit here because customer.loan_limit
# defaults to 0 and has never been set — that is exactly what this feature
# is for. The limit is set in set_loan_limits_for_batch as:
#   new_limit = existing_loan_balance + requested_amount
# ─────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=15)
def check_single_eligibility(self, request_id: str):
    from apps.loans.models import LoanRequest
    from apps.api.lms_client import get_customer_balances

    try:
        req = LoanRequest.objects.select_related('organization').get(id=request_id)
    except LoanRequest.DoesNotExist:
        log.error('check_single_eligibility: request %s not found', request_id)
        return 'not_found'

    req.status = LoanRequest.STATUS_ELIGIBILITY_CHECKING
    req.save(update_fields=['status'])

    try:
        balances = get_customer_balances(req.phone_number)
    except Exception as exc:
        log.warning(
            'check_single_eligibility: LMS call failed for %s: %s', req.phone_number, exc,
        )
        raise self.retry(exc=exc)

    # Hard gate — customer must exist and be registered under this partner's org
    if balances is None:
        req.status               = LoanRequest.STATUS_INELIGIBLE
        req.ineligibility_reason = 'Customer not found or not registered under this organisation.'
        req.save(update_fields=['status', 'ineligibility_reason'])
        return 'ineligible:not_found'

    # Store current balance data for audit and for set_loan_limits_for_batch to use
    # when computing: new_limit = existing_loan_balance + requested_amount
    req.accessible_loan_limit = Decimal(str(balances.get('accessible_loan_limit', '0') or '0'))
    req.existing_loan_balance = Decimal(str(balances.get('loan_balance', '0') or '0'))
    req.status                = LoanRequest.STATUS_ELIGIBLE
    req.save(update_fields=['status', 'accessible_loan_limit', 'existing_loan_balance'])
    return 'eligible'


# ─────────────────────────────────────────────────────────────
# Step 4 — Finalize eligibility, create batch (DRAFT)
# ─────────────────────────────────────────────────────────────

@shared_task
def finalize_eligibility(results, upload_id: str):
    """
    Chord callback after all eligibility checks complete.
    Creates LoanRequestBatch in DRAFT status.
    Admin then approves via approve_and_set_limits action
    which fires set_loan_limits_for_batch — the terminal step.
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
            'batch=%s status=DRAFT — awaiting admin approval to set loan limits',
            upload_id, upload.eligible_rows, upload.ineligible_rows, batch.id,
        )

    except Exception as exc:
        log.exception('finalize_eligibility error for upload %s: %s', upload_id, exc)


# ─────────────────────────────────────────────────────────────
# Step 5 — Set loan limits (terminal step — no loan dispatch)
# Triggered by admin approve_and_set_limits action.
# Sets customer.loan_limit = existing_loan_balance + requested_amount
# on the LMS so the customer can self-serve via USSD or mobile app.
# ─────────────────────────────────────────────────────────────

@shared_task(bind=True)
def set_loan_limits_for_batch(self, batch_id: str):
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
        batch.status = LoanRequestBatch.STATUS_COMPLETE
        batch.save(update_fields=['status'])
        return

    # new_limit = existing_loan_balance + requested_amount
    # This ensures LoanDisk sees a ceiling that covers both outstanding
    # balance and the new amount the employee is being granted this cycle.
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
        'set_loan_limits_for_batch: setting limits for %d customers (batch=%s) '
        'using formula: existing_balance + requested_amount',
        len(customers_payload), batch_id,
    )

    result       = bulk_set_loan_limits(customers_payload)
    result_data  = result.get('data', {})
    results_list = result_data.get('results', [])
    result_by_phone = {r['phone_number']: r for r in results_list}

    successful = 0
    failed     = 0

    for req in eligible_requests:
        phone_result = result_by_phone.get(req.phone_number)
        if phone_result and phone_result.get('status') == 'updated':
            req.status = LoanRequest.STATUS_SUCCESS
            req.save(update_fields=['status'])
            successful += 1
            log.info(
                'set_loan_limits_for_batch: limit set for %s → %s',
                req.phone_number,
                (req.existing_loan_balance or Decimal('0.00')) + req.requested_amount,
            )
        else:
            req.status         = LoanRequest.STATUS_FAILED
            req.failure_reason = (
                f"Loan limit update failed: {phone_result.get('reason', 'LMS error')}"
                if phone_result else 'No result returned from LMS'
            )
            req.save(update_fields=['status', 'failure_reason'])
            failed += 1
            log.warning(
                'set_loan_limits_for_batch: limit set FAILED for %s — %s',
                req.phone_number,
                phone_result.get('reason') if phone_result else 'missing from LMS response',
            )

    batch.successful_count = successful
    batch.failed_count     = failed
    batch.total_requests   = len(eligible_requests)
    batch.status = (
        LoanRequestBatch.STATUS_COMPLETE
        if failed == 0
        else LoanRequestBatch.STATUS_FAILED
        if successful == 0
        else LoanRequestBatch.STATUS_COMPLETE  # partial success — still mark complete
    )
    batch.save(update_fields=['status', 'successful_count', 'failed_count', 'total_requests'])

    log.info(
        'set_loan_limits_for_batch: batch=%s DONE — set=%d failed=%d status=%s. '
        'Customers can now apply via USSD or mobile app.',
        batch_id, successful, failed, batch.status,
    )