"""
apps/loans/models.py

Tracks HR-initiated bulk loan requests.

Flow:
  LoanRequestUpload (approval_pending → approved → processing → done/partial/failed)
      └── LoanRequest (queued → eligibility_checking → eligible/ineligible → processing → success/failed/skipped)
              └── LoanRequestBatch (draft → approved → dispatching → complete/failed)

Key design decisions:
  - Upload is APPROVAL_PENDING on creation — admin must approve before Celery touches it.
  - Eligibility (accessible_loan_limit vs requested_amount) is checked async via Celery
    before any borrow_loan call is made.
  - idempotency_key prevents duplicate LMS submissions on retry.
  - LoanRequest.lms_loan_id stores the LoanDisk loan ID on success.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, FileExtensionValidator
from django.db import models
from django.utils import timezone

from apps.base.models import BaseModel
from apps.organizations.models import CheckoffOrganizationMirror


def loan_upload_path(instance, filename):
    return f'loans/{instance.organization.code}/{timezone.now():%Y/%m}/{filename}'


# ─────────────────────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────────────────────

class LoanRequestUpload(BaseModel):
    STATUS_APPROVAL_PENDING = 'approval_pending'
    STATUS_APPROVED         = 'approved'
    STATUS_PROCESSING       = 'processing'
    STATUS_DONE             = 'done'
    STATUS_PARTIAL          = 'partial'
    STATUS_FAILED           = 'failed'

    STATUS_CHOICES = [
        (STATUS_APPROVAL_PENDING, 'Approval Pending'),
        (STATUS_APPROVED,         'Approved'),
        (STATUS_PROCESSING,       'Processing'),
        (STATUS_DONE,             'Completed'),
        (STATUS_PARTIAL,          'Partially Processed'),
        (STATUS_FAILED,           'Failed'),
    ]

    organization    = models.ForeignKey(
        CheckoffOrganizationMirror, on_delete=models.PROTECT,
        related_name='loan_request_uploads',
    )
    uploaded_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name='loan_request_uploads',
    )
    approved_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='approved_loan_uploads',
    )
    approved_at     = models.DateTimeField(null=True, blank=True)
    file            = models.FileField(
        upload_to=loan_upload_path,
        validators=[FileExtensionValidator(allowed_extensions=['xlsx', 'xls'])],
    )
    original_filename = models.CharField(max_length=255)
    status          = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_APPROVAL_PENDING,
    )
    loan_period     = models.DateField(
        help_text='Loan period / month this upload covers (YYYY-MM-01).',
    )
    total_rows      = models.PositiveIntegerField(default=0)
    processed_rows  = models.PositiveIntegerField(default=0)
    failed_rows     = models.PositiveIntegerField(default=0)
    eligible_rows   = models.PositiveIntegerField(default=0)
    ineligible_rows = models.PositiveIntegerField(default=0)
    error_log       = models.JSONField(default=list, blank=True)
    notes           = models.TextField(blank=True)
    celery_task_id  = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name        = 'Loan Request Upload'
        verbose_name_plural = 'Loan Request Uploads'
        ordering            = ('-date_created',)
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['organization', 'loan_period']),
        ]

    def __str__(self):
        return f'{self.organization.code} | {self.loan_period:%Y-%m} | {self.status}'

    @property
    def progress_percent(self):
        if self.total_rows == 0:
            return 0
        return round((self.processed_rows / self.total_rows) * 100, 1)

    def approve(self, user):
        self.status      = self.STATUS_APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save(update_fields=['status', 'approved_by', 'approved_at'])


# ─────────────────────────────────────────────────────────────
# Individual loan request row
# ─────────────────────────────────────────────────────────────

class LoanRequest(BaseModel):
    # Eligibility + dispatch statuses
    STATUS_QUEUED               = 'queued'
    STATUS_ELIGIBILITY_CHECKING = 'eligibility_checking'
    STATUS_ELIGIBLE             = 'eligible'
    STATUS_INELIGIBLE           = 'ineligible'
    STATUS_PROCESSING           = 'processing'
    STATUS_SUCCESS              = 'success'
    STATUS_FAILED               = 'failed'
    STATUS_SKIPPED              = 'skipped'

    STATUS_CHOICES = [
        (STATUS_QUEUED,               'Queued'),
        (STATUS_ELIGIBILITY_CHECKING, 'Checking Eligibility'),
        (STATUS_ELIGIBLE,             'Eligible'),
        (STATUS_INELIGIBLE,           'Ineligible'),
        (STATUS_PROCESSING,           'Processing'),
        (STATUS_SUCCESS,              'Success'),
        (STATUS_FAILED,               'Failed'),
        (STATUS_SKIPPED,              'Skipped'),
    ]

    upload          = models.ForeignKey(
        LoanRequestUpload, on_delete=models.CASCADE, related_name='loan_requests',
    )
    organization    = models.ForeignKey(
        CheckoffOrganizationMirror, on_delete=models.PROTECT,
        related_name='loan_requests',
    )

    # From the uploaded Excel
    phone_number    = models.CharField(max_length=20)
    employee_name   = models.CharField(max_length=200, blank=True)
    employee_id     = models.CharField(max_length=100, blank=True)
    requested_amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal('1.00'))],
    )
    row_number      = models.PositiveIntegerField(default=0)
    reference       = models.CharField(max_length=100, blank=True)

    # Eligibility data fetched from LMS
    loan_limit           = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text='Customer loan_limit from LMS at time of eligibility check.',
    )
    existing_loan_balance = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text='Customer total active loan balance from LMS.',
    )
    accessible_loan_limit = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text='accessible_loan_limit = loan_limit - existing_balance.',
    )
    ineligibility_reason = models.TextField(blank=True)

    # Dispatch tracking
    status          = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    attempts        = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    lms_response    = models.JSONField(default=dict, blank=True)
    failure_reason  = models.TextField(blank=True)
    lms_loan_id     = models.CharField(
        max_length=100, blank=True,
        help_text='LoanDisk loan_id returned on successful disbursement.',
    )
    idempotency_key = models.CharField(max_length=200, unique=True)

    class Meta:
        verbose_name        = 'Loan Request'
        verbose_name_plural = 'Loan Requests'
        ordering            = ('-date_created',)
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['phone_number', 'upload']),
            models.Index(fields=['idempotency_key']),
            models.Index(fields=['upload', 'status']),
        ]

    def __str__(self):
        return f'{self.phone_number} | {self.requested_amount} | {self.status}'

    def build_idempotency_key(self):
        import hashlib
        raw = (
            f'loan:{self.organization_id}:{self.phone_number}:'
            f'{self.requested_amount}:{self.upload_id}:{self.row_number}'
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def save(self, *args, **kwargs):
        if not self.idempotency_key:
            self.idempotency_key = self.build_idempotency_key()
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────
# Batch (groups all requests for one upload)
# ─────────────────────────────────────────────────────────────

class LoanRequestBatch(BaseModel):
    STATUS_DRAFT       = 'draft'
    STATUS_APPROVED    = 'approved'
    STATUS_DISPATCHING = 'dispatching'
    STATUS_COMPLETE    = 'complete'
    STATUS_FAILED      = 'failed'

    STATUS_CHOICES = [
        (STATUS_DRAFT,       'Draft'),
        (STATUS_APPROVED,    'Approved'),
        (STATUS_DISPATCHING, 'Dispatching'),
        (STATUS_COMPLETE,    'Complete'),
        (STATUS_FAILED,      'Failed'),
    ]

    organization      = models.ForeignKey(
        CheckoffOrganizationMirror, on_delete=models.PROTECT,
        related_name='loan_request_batches',
    )
    upload            = models.OneToOneField(
        LoanRequestUpload, on_delete=models.PROTECT,
        related_name='loan_request_batch',
    )
    status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    total_requests    = models.PositiveIntegerField(default=0)
    total_amount      = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    eligible_count    = models.PositiveIntegerField(default=0)
    ineligible_count  = models.PositiveIntegerField(default=0)
    successful_count  = models.PositiveIntegerField(default=0)
    failed_count      = models.PositiveIntegerField(default=0)
    skipped_count     = models.PositiveIntegerField(default=0)
    successful_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    approved_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='approved_loan_batches',
    )
    approved_at       = models.DateTimeField(null=True, blank=True)
    celery_task_id    = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name        = 'Loan Request Batch'
        verbose_name_plural = 'Loan Request Batches'
        ordering            = ('-date_created',)
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['status', 'date_created']),
        ]

    def __str__(self):
        return f'LoanBatch {str(self.id)[:8]} | {self.organization.code} | {self.status}'

    def refresh_counters(self):
        from django.db.models import Sum as DSum
        qs = LoanRequest.objects.filter(upload=self.upload)
        self.total_requests   = qs.count()
        self.eligible_count   = qs.filter(status=LoanRequest.STATUS_ELIGIBLE).count()
        self.ineligible_count = qs.filter(status=LoanRequest.STATUS_INELIGIBLE).count()
        self.successful_count = qs.filter(status=LoanRequest.STATUS_SUCCESS).count()
        self.failed_count     = qs.filter(status=LoanRequest.STATUS_FAILED).count()
        self.skipped_count    = qs.filter(status=LoanRequest.STATUS_SKIPPED).count()
        self.successful_amount = (
            qs.filter(status=LoanRequest.STATUS_SUCCESS)
              .aggregate(t=DSum('requested_amount'))['t'] or Decimal('0.00')
        )
        self.save(update_fields=[
            'total_requests', 'eligible_count', 'ineligible_count',
            'successful_count', 'failed_count', 'skipped_count', 'successful_amount',
        ])