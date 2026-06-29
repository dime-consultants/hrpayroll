# apps/payroll/models.py
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, FileExtensionValidator
from django.db import models
from django.utils import timezone

from apps.base.models import BaseModel
from apps.organizations.models import CheckoffOrganizationMirror


def payroll_upload_path(instance, filename):
    return f'payroll/{instance.organization.code}/{timezone.now():%Y/%m}/{filename}'


class PayrollUpload(BaseModel):
    STATUS_APPROVAL_PENDING = 'approval_pending'
    STATUS_APPROVED   = 'approved'
    STATUS_PENDING    = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_DONE       = 'done'
    STATUS_FAILED     = 'failed'
    STATUS_PARTIAL    = 'partial'

    STATUS_CHOICES = [
        (STATUS_APPROVAL_PENDING, 'Approval Pending'),
        (STATUS_APPROVED,   'Approved'),
        (STATUS_PENDING,    'Pending'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_DONE,       'Completed'),
        (STATUS_FAILED,     'Failed'),
        (STATUS_PARTIAL,    'Partially Processed'),
    ]

    organization = models.ForeignKey(
        CheckoffOrganizationMirror, on_delete=models.PROTECT,
        related_name='payroll_uploads'
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,   # ← was: User (direct import from auth)
        null=True, on_delete=models.SET_NULL,
        related_name='payroll_uploads'
    )
    file = models.FileField(
        upload_to=payroll_upload_path,
        validators=[FileExtensionValidator(allowed_extensions=['xlsx', 'xls', 'csv'])]
    )
    original_filename = models.CharField(max_length=255)
    status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    total_rows        = models.PositiveIntegerField(default=0)
    processed_rows    = models.PositiveIntegerField(default=0)
    failed_rows       = models.PositiveIntegerField(default=0)
    error_log         = models.JSONField(default=list, blank=True)
    payroll_period    = models.DateField(help_text='Payroll period this upload covers (YYYY-MM-01).')
    notes             = models.TextField(blank=True)
    celery_task_id    = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = 'Payroll Upload'
        verbose_name_plural = 'Payroll Uploads'
        ordering = ('-date_created',)
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['organization', 'payroll_period']),
        ]

    def __str__(self):
        return f'{self.organization.code} | {self.payroll_period:%Y-%m} | {self.status}'

    @property
    def success_rows(self):
        return self.processed_rows - self.failed_rows

    @property
    def progress_percent(self):
        if self.total_rows == 0:
            return 0
        return round((self.processed_rows / self.total_rows) * 100, 1)


class SalaryDeduction(BaseModel):
    STATUS_QUEUED     = 'queued'
    STATUS_PROCESSING = 'processing'
    STATUS_SUCCESS    = 'success'
    STATUS_FAILED     = 'failed'
    STATUS_SKIPPED    = 'skipped'

    STATUS_CHOICES = [
        (STATUS_QUEUED,     'Queued'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_SUCCESS,    'Success'),
        (STATUS_FAILED,     'Failed'),
        (STATUS_SKIPPED,    'Skipped'),
    ]

    upload       = models.ForeignKey(PayrollUpload, on_delete=models.CASCADE, related_name='deductions')
    organization = models.ForeignKey(
        CheckoffOrganizationMirror, on_delete=models.PROTECT,
        related_name='deductions'
    )
    phone_number    = models.CharField(max_length=20)
    employee_name   = models.CharField(max_length=200, blank=True)
    employee_id     = models.CharField(max_length=100, blank=True)
    amount          = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal('1.00'))]
    )
    deduction_date  = models.DateField()
    reference       = models.CharField(max_length=100, blank=True)
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    attempts        = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    lms_response    = models.JSONField(default=dict, blank=True)
    failure_reason  = models.TextField(blank=True)
    row_number      = models.PositiveIntegerField(default=0)
    idempotency_key = models.CharField(max_length=200, unique=True)

    class Meta:
        verbose_name = 'Salary Deduction'
        verbose_name_plural = 'Salary Deductions'
        ordering = ('-date_created',)
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['phone_number', 'deduction_date']),
            models.Index(fields=['idempotency_key']),
            models.Index(fields=['upload', 'status']),
        ]

    def __str__(self):
        return f'{self.phone_number} | {self.amount} | {self.status}'

    def build_idempotency_key(self):
        import hashlib
        raw = (
            f'{self.organization_id}:{self.phone_number}:'
            f'{self.amount}:{self.deduction_date}:{self.reference}'
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def save(self, *args, **kwargs):
        if not self.idempotency_key:
            self.idempotency_key = self.build_idempotency_key()
        super().save(*args, **kwargs)