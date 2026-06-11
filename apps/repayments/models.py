# apps/repayments/models.py
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.base.models import BaseModel
from apps.organizations.models import CheckoffOrganizationMirror
from apps.payroll.models import SalaryDeduction


class IdempotencyKey(models.Model):
    """
    Not a BaseModel — primary key is the key string itself, not a UUID.
    Kept as a plain model intentionally.
    """
    STATUS_CLAIMED = 'claimed'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED  = 'failed'

    STATUS_CHOICES = [
        (STATUS_CLAIMED, 'Claimed'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED,  'Failed'),
    ]

    key              = models.CharField(max_length=200, primary_key=True)
    organization     = models.ForeignKey(
        CheckoffOrganizationMirror, on_delete=models.CASCADE,
        related_name='idempotency_keys'
    )
    status           = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_CLAIMED)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at       = models.DateTimeField(default=timezone.now)
    expires_at       = models.DateTimeField()

    class Meta:
        verbose_name = 'Idempotency Key'
        verbose_name_plural = 'Idempotency Keys'
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f'{self.key[:24]}… [{self.status}]'

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at


class RepaymentBatch(BaseModel):
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
        related_name='repayment_batches'
    )
    upload            = models.OneToOneField(
        'payroll.PayrollUpload', on_delete=models.PROTECT,
        related_name='repayment_batch'
    )
    status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    total_deductions  = models.PositiveIntegerField(default=0)
    total_amount      = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    successful_count  = models.PositiveIntegerField(default=0)
    failed_count      = models.PositiveIntegerField(default=0)
    skipped_count     = models.PositiveIntegerField(default=0)
    successful_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    approved_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL,   # ← was: 'auth.User' (hardcoded string)
        null=True, blank=True,
        on_delete=models.SET_NULL, related_name='approved_batches'
    )
    approved_at    = models.DateTimeField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = 'Repayment Batch'
        verbose_name_plural = 'Repayment Batches'
        ordering = ('-date_created',)
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['status', 'date_created']),
        ]

    def __str__(self):
        return f'Batch {str(self.id)[:8]} | {self.organization.code} | {self.status}'

    def approve(self, user):
        self.status = self.STATUS_APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save(update_fields=['status', 'approved_by', 'approved_at'])

    def refresh_counters(self):
        from django.db.models import Sum as DSum
        qs = self.records.all()
        self.total_deductions = qs.count()
        self.successful_count = qs.filter(status=RepaymentRecord.STATUS_SUCCESS).count()
        self.failed_count     = qs.filter(status=RepaymentRecord.STATUS_FAILED).count()
        self.skipped_count    = qs.filter(status=RepaymentRecord.STATUS_SKIPPED).count()
        self.successful_amount = (
            qs.filter(status=RepaymentRecord.STATUS_SUCCESS)
              .aggregate(t=DSum('amount_sent'))['t'] or Decimal('0.00')
        )
        self.save(update_fields=[
            'total_deductions', 'successful_count', 'failed_count',
            'skipped_count', 'successful_amount',
        ])


class RepaymentRecord(BaseModel):
    STATUS_PENDING = 'pending'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED  = 'failed'
    STATUS_SKIPPED = 'skipped'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED,  'Failed'),
        (STATUS_SKIPPED, 'Skipped'),
    ]

    batch            = models.ForeignKey(RepaymentBatch, on_delete=models.CASCADE, related_name='records')
    deduction        = models.ForeignKey(SalaryDeduction, on_delete=models.CASCADE,
                                         related_name='repayment_records')
    organization     = models.ForeignKey(
        CheckoffOrganizationMirror, on_delete=models.PROTECT,
        related_name='repayment_records'
    )
    idempotency_key  = models.ForeignKey(
        IdempotencyKey, null=True, blank=True, on_delete=models.SET_NULL
    )
    phone_number     = models.CharField(max_length=20)
    amount_requested = models.DecimalField(max_digits=14, decimal_places=2)
    amount_sent      = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('0.00'),
        help_text='Actual amount sent (capped at loan balance).'
    )
    status           = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    attempt_number   = models.PositiveSmallIntegerField(default=1)
    lms_response_code = models.CharField(max_length=20, blank=True)
    lms_response_body = models.JSONField(default=dict, blank=True)
    failure_reason   = models.TextField(blank=True)
    duration_ms      = models.PositiveIntegerField(default=0)
    dispatched_at    = models.DateTimeField(default=timezone.now)
    completed_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Repayment Record'
        verbose_name_plural = 'Repayment Records'
        ordering = ('-dispatched_at',)
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['phone_number', 'dispatched_at']),
            models.Index(fields=['batch', 'status']),
            models.Index(fields=['deduction']),
        ]

    def __str__(self):
        return f'{self.phone_number} | {self.amount_sent} | {self.status}'