"""
Celery task pipeline:

  parse_payroll_upload      → reads Excel/CSV, creates SalaryDeduction rows
  build_repayment_batch     → groups deductions into a RepaymentBatch (DRAFT)
  dispatch_repayment_batch  → fan-out chord, one task per deduction
  dispatch_single_repayment → calls LMS, handles retries
  finalize_batch            → chord callback, refreshes counters
  cleanup_expired_idempotency_keys → nightly maintenance
"""
import csv
import io
import logging
import time
from decimal import Decimal, InvalidOperation

from celery import shared_task, chord, group
from django.db import transaction as db_transaction
from django.utils import timezone

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_COUNTDOWN = 30  # base seconds, doubles each retry


# ─────────────────────────────────────────────────────────────
# Step 1 — Parse upload file
# ─────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def parse_payroll_upload(self, upload_id: str):
    from apps.payroll.models import PayrollUpload, SalaryDeduction

    try:
        upload = PayrollUpload.objects.select_related('organization').get(id=upload_id)
    except PayrollUpload.DoesNotExist:
        log.error('parse_payroll_upload: upload %s not found', upload_id)
        return

    upload.status = PayrollUpload.STATUS_PROCESSING
    upload.celery_task_id = self.request.id
    upload.save(update_fields=['status', 'celery_task_id'])

    errors = []
    rows_created = 0

    try:
        filename = upload.original_filename.lower()
        rows = _parse_csv(upload.file) if filename.endswith('.csv') else _parse_excel(upload.file)

        upload.total_rows = len(rows)
        upload.save(update_fields=['total_rows'])

        bulk_deductions = []
        for i, row in enumerate(rows, start=2):
            result = _validate_row(row, i, upload)
            if result['error']:
                errors.append(result)
                continue
            deduction = SalaryDeduction(
                upload=upload,
                organization=upload.organization,
                phone_number=result['phone_number'],
                employee_name=result.get('employee_name', ''),
                employee_id=result.get('employee_id', ''),
                amount=result['amount'],
                deduction_date=result['deduction_date'],
                reference=result.get('reference', ''),
                row_number=i,
                status=SalaryDeduction.STATUS_QUEUED,
            )
            bulk_deductions.append(deduction)

        with db_transaction.atomic():
            # ignore_conflicts skips rows whose idempotency_key already exists
            SalaryDeduction.objects.bulk_create(bulk_deductions, ignore_conflicts=True)
            rows_created = len(bulk_deductions)

        upload.processed_rows = rows_created
        upload.failed_rows = len(errors)
        upload.error_log = errors
        upload.status = (
            PayrollUpload.STATUS_DONE if not errors else PayrollUpload.STATUS_PARTIAL
        )
        upload.save(update_fields=['processed_rows', 'failed_rows', 'error_log', 'status'])
        log.info('parse_payroll_upload: upload=%s created=%d errors=%d',
                 upload_id, rows_created, len(errors))

        build_repayment_batch.delay(upload_id)

    except Exception as exc:
        log.exception('parse_payroll_upload failed for %s: %s', upload_id, exc)
        upload.status = PayrollUpload.STATUS_FAILED
        upload.error_log = [{'error': str(exc)}]
        upload.save(update_fields=['status', 'error_log'])
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────
# Step 2 — Build batch
# ─────────────────────────────────────────────────────────────

@shared_task(bind=True)
def build_repayment_batch(self, upload_id: str):
    from apps.payroll.models import PayrollUpload, SalaryDeduction
    from apps.repayments.models import RepaymentBatch
    from django.db.models import Sum

    try:
        upload = PayrollUpload.objects.get(id=upload_id)
        deductions = SalaryDeduction.objects.filter(
            upload=upload, status=SalaryDeduction.STATUS_QUEUED
        )
        total_amount = deductions.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

        batch, created = RepaymentBatch.objects.get_or_create(
            upload=upload,
            defaults={
                'organization': upload.organization,
                'total_deductions': deductions.count(),
                'total_amount': total_amount,
                'status': RepaymentBatch.STATUS_DRAFT,
            }
        )
        if not created:
            batch.total_deductions = deductions.count()
            batch.total_amount = total_amount
            batch.save(update_fields=['total_deductions', 'total_amount'])

        log.info('build_repayment_batch: batch=%s deductions=%d total=%s',
                 batch.id, batch.total_deductions, batch.total_amount)
    except Exception as exc:
        log.exception('build_repayment_batch failed for upload %s: %s', upload_id, exc)
        raise


# ─────────────────────────────────────────────────────────────
# Step 3 — Dispatch batch (fan-out)
# ─────────────────────────────────────────────────────────────

@shared_task(bind=True)
def dispatch_repayment_batch(self, batch_id: str):
    from apps.repayments.models import RepaymentBatch
    from apps.payroll.models import SalaryDeduction

    try:
        batch = RepaymentBatch.objects.select_related('organization').get(id=batch_id)
    except RepaymentBatch.DoesNotExist:
        log.error('dispatch_repayment_batch: batch %s not found', batch_id)
        return

    if batch.status not in (RepaymentBatch.STATUS_APPROVED, RepaymentBatch.STATUS_DRAFT):
        log.warning('dispatch_repayment_batch: batch %s is %s — skipping', batch_id, batch.status)
        return

    batch.status = RepaymentBatch.STATUS_DISPATCHING
    batch.save(update_fields=['status'])

    deduction_ids = list(
        batch.upload.deductions.filter(
            status__in=[SalaryDeduction.STATUS_QUEUED, SalaryDeduction.STATUS_FAILED]
        ).values_list('id', flat=True)
    )

    if not deduction_ids:
        batch.status = RepaymentBatch.STATUS_COMPLETE
        batch.save(update_fields=['status'])
        return

    job = chord(
        group(dispatch_single_repayment.s(str(d_id), batch_id) for d_id in deduction_ids),
        finalize_batch.s(batch_id)
    )
    job.apply_async()
    log.info('dispatch_repayment_batch: dispatched %d tasks for batch %s',
             len(deduction_ids), batch_id)


# ─────────────────────────────────────────────────────────────
# Step 4 — Single repayment
# ─────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=MAX_RETRIES, acks_late=True, reject_on_worker_lost=True)
def dispatch_single_repayment(self, deduction_id: str, batch_id: str):
    from apps.payroll.models import SalaryDeduction
    from apps.repayments.models import RepaymentBatch, RepaymentRecord
    from apps.api.lms_client import send_repayment
    from apps.api.error_handling import get_retry_countdown

    try:
        deduction = SalaryDeduction.objects.select_related('organization').get(id=deduction_id)
    except SalaryDeduction.DoesNotExist:
        log.error('dispatch_single_repayment: deduction %s not found', deduction_id)
        return 'not_found'

    try:
        batch = RepaymentBatch.objects.get(id=batch_id)
    except RepaymentBatch.DoesNotExist:
        return 'batch_not_found'

    deduction.status = SalaryDeduction.STATUS_PROCESSING
    deduction.last_attempt_at = timezone.now()
    deduction.attempts += 1
    deduction.save(update_fields=['status', 'last_attempt_at', 'attempts'])

    result = send_repayment(
        phone_number=deduction.phone_number,
        amount=deduction.amount,
        collection_date=deduction.deduction_date.strftime('%Y-%m-%d'),
        idempotency_key=deduction.idempotency_key,
        organization_code=deduction.organization.code,
    )

    if result.success:
        final_status = SalaryDeduction.STATUS_SUCCESS
    elif result.code == 'no_balance':
        final_status = SalaryDeduction.STATUS_SKIPPED
    else:
        final_status = SalaryDeduction.STATUS_FAILED

    deduction.status = final_status
    deduction.lms_response = result.response_body
    if not result.success:
        deduction.failure_reason = result.error
    deduction.save(update_fields=['status', 'lms_response', 'failure_reason'])

    RepaymentRecord.objects.create(
        batch=batch,
        deduction=deduction,
        organization=deduction.organization,
        phone_number=deduction.phone_number,
        amount_requested=deduction.amount,
        amount_sent=result.amount_sent,
        status=(
            RepaymentRecord.STATUS_SUCCESS if result.success
            else (RepaymentRecord.STATUS_SKIPPED if result.code == 'no_balance'
                  else RepaymentRecord.STATUS_FAILED)
        ),
        attempt_number=deduction.attempts,
        lms_response_code=result.code,
        lms_response_body=result.response_body,
        failure_reason=result.error,
        duration_ms=result.duration_ms,
        completed_at=timezone.now(),
    )

    # Intelligent retry logic based on error classification
    if not result.success and result.code not in ('no_balance', 'lock_timeout', 'circuit_open'):
        # Extract error classification if available
        error_classification = result.response_body.get('classification') if isinstance(result.response_body, dict) else None
        
        if error_classification:
            from apps.api.error_handling import ErrorClassification
            classification = ErrorClassification(error_classification)
        else:
            # Fallback: infer from code
            if result.code in ('connection_error', 'timeout', 'network_error'):
                classification = ErrorClassification.NETWORK
            elif result.code in ('http_error',):
                classification = ErrorClassification.TRANSIENT
            else:
                classification = ErrorClassification.UNKNOWN
        
        # Get intelligent retry countdown
        countdown = get_retry_countdown(self.request.retries + 1, classification)
        
        if countdown > 0 and self.request.retries < MAX_RETRIES:
            log.info(
                'Retrying deduction %s in %ds (attempt %d/%d) classification=%s',
                deduction_id, countdown, self.request.retries + 1, MAX_RETRIES, classification.value
            )
            raise self.retry(countdown=countdown)
        else:
            log.warning(
                'Not retrying deduction %s (classification=%s or max retries reached)',
                deduction_id, classification.value if countdown <= 0 else 'max_retries'
            )
    elif result.code == 'circuit_open':
        # Circuit breaker is open — retry aggressively to wait for recovery
        if self.request.retries < MAX_RETRIES:
            countdown = 120  # Wait 2 minutes before retrying
            log.info(
                'Circuit breaker open for deduction %s — retrying in %ds (attempt %d/%d)',
                deduction_id, countdown, self.request.retries + 1, MAX_RETRIES
            )
            raise self.retry(countdown=countdown)

    return final_status


# ─────────────────────────────────────────────────────────────
# Step 5 — Finalize batch
# ─────────────────────────────────────────────────────────────

@shared_task
def finalize_batch(results, batch_id: str):
    from apps.repayments.models import RepaymentBatch
    try:
        batch = RepaymentBatch.objects.get(id=batch_id)
        batch.refresh_counters()
        batch.status = (
            RepaymentBatch.STATUS_FAILED
            if batch.failed_count == batch.total_deductions
            else RepaymentBatch.STATUS_COMPLETE
        )
        batch.save(update_fields=['status'])
        log.info('finalize_batch: %s → %s (ok=%d fail=%d skip=%d)',
                 batch_id, batch.status, batch.successful_count,
                 batch.failed_count, batch.skipped_count)
    except Exception as exc:
        log.exception('finalize_batch error for batch %s: %s', batch_id, exc)


# ─────────────────────────────────────────────────────────────
# Maintenance
# ─────────────────────────────────────────────────────────────

@shared_task
def cleanup_expired_idempotency_keys():
    from apps.repayments.models import IdempotencyKey
    deleted, _ = IdempotencyKey.objects.filter(expires_at__lt=timezone.now()).delete()
    log.info('cleanup_expired_idempotency_keys: deleted %d', deleted)
    return deleted


# ─────────────────────────────────────────────────────────────
# File parsing helpers
# ─────────────────────────────────────────────────────────────

COLUMN_ALIASES = {
    'phone_number': ['phone_number', 'phone', 'msisdn', 'mobile', 'telephone'],
    'amount':       ['amount', 'deduction', 'deduction_amount', 'repayment', 'repayment_amount'],
    'deduction_date': ['deduction_date', 'date', 'payroll_date', 'payment_date'],
    'employee_name': ['employee_name', 'name', 'full_name', 'employee'],
    'employee_id':   ['employee_id', 'staff_id', 'payroll_number', 'emp_id'],
    'reference':     ['reference', 'ref', 'receipt', 'transaction_ref'],
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
    import openpyxl
    file_field.seek(0)
    wb = openpyxl.load_workbook(file_field, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h).strip() if h is not None else '' for h in rows[0]]
    col_map = _normalize_headers(headers)
    result = []
    for row in rows[1:]:
        if all(c is None for c in row):
            continue
        result.append({k: row[v] for k, v in col_map.items() if v < len(row)})
    return result


def _parse_csv(file_field):
    file_field.seek(0)
    content = file_field.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(content))
    fieldnames = reader.fieldnames or []
    headers = [h.strip().lower() for h in fieldnames]
    col_map = _normalize_headers(headers)
    result = []
    for row in reader:
        mapped = {}
        for canonical, alias_idx in col_map.items():
            original_key = fieldnames[alias_idx]
            mapped[canonical] = row.get(original_key, '').strip()
        result.append(mapped)
    return result


def _validate_row(row: dict, row_num: int, upload) -> dict:
    from apps.api.validators import normalize_phone, validate_phone
    from dateutil.parser import parse as date_parse

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

    raw_date = str(row.get('deduction_date') or '').strip()
    try:
        deduction_date = date_parse(raw_date).date() if raw_date else upload.payroll_period
    except Exception:
        deduction_date = upload.payroll_period
        errors.append(f'Row {row_num}: invalid date "{raw_date}" — using payroll period')

    if errors:
        return {'error': True, 'messages': errors, 'row': row_num}

    return {
        'error': False,
        'phone_number': phone,
        'amount': amount,
        'deduction_date': deduction_date,
        'employee_name': str(row.get('employee_name') or '').strip()[:200],
        'employee_id': str(row.get('employee_id') or '').strip()[:100],
        'reference': str(row.get('reference') or '').strip()[:100],
    }
