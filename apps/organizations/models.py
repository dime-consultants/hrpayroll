# apps/organizations/models.py
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.base.models import BaseModel


class CheckoffOrganizationMirror(BaseModel):
    """
    Local mirror of the LMS CheckoffOrganization.
    Every HR user and all payroll data is scoped to one of these.
    """
    lms_id = models.CharField(
        max_length=100, unique=True,
        help_text='UUID of this org in the Dime LMS.'
    )
    name         = models.CharField(max_length=200)
    code         = models.CharField(max_length=50, unique=True)
    email        = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=30, blank=True, null=True)
    contact_name = models.CharField(max_length=100, blank=True, null=True)
    is_active    = models.BooleanField(default=True)

    class Meta:
        verbose_name        = 'Checkoff Organization'
        verbose_name_plural = 'Checkoff Organizations'
        ordering            = ('name',)

    def __str__(self):
        return f'{self.name} ({self.code})'


class HRUser(BaseModel):
    ROLE_CHOICES = [
        ('admin',   'HR Admin'),
        ('officer', 'Payroll Officer'),
        ('viewer',  'Read-Only Viewer'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,       # ← was: User
        on_delete=models.CASCADE,
        related_name='hr_profile',
    )
    organization = models.ForeignKey(
        CheckoffOrganizationMirror,
        on_delete=models.PROTECT,
        related_name='hr_users',
    )
    role       = models.CharField(max_length=20, choices=ROLE_CHOICES, default='officer')
    is_active  = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,       # ← was: User
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='created_hr_users',
    )

    class Meta:
        verbose_name        = 'HR User'
        verbose_name_plural = 'HR Users'
        ordering            = ('user__email',)  # ← username → email (your User has no username)

    def __str__(self):
        return f'{self.user.full_name or self.user.email} — {self.organization.name} [{self.role}]'

    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def can_upload(self):
        return self.role in ('admin', 'officer')


class AuditLog(BaseModel):
    ACTION_CHOICES = [
        ('upload',             'Payroll Upload'),
        ('deduction_create',   'Deduction Created'),
        ('batch_submit',       'Repayment Batch Submitted'),
        ('batch_approve',      'Repayment Batch Approved'),
        ('repayment_sent',     'Repayment Sent to LMS'),
        ('repayment_failed',   'Repayment Failed'),
        ('user_create',        'HR User Created'),
        ('user_deactivate',    'HR User Deactivated'),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,       # ← was: User
        null=True,
        on_delete=models.SET_NULL,
    )
    organization = models.ForeignKey(
        CheckoffOrganizationMirror, null=True, on_delete=models.SET_NULL
    )
    action      = models.CharField(max_length=50, choices=ACTION_CHOICES)
    object_id   = models.CharField(max_length=100, blank=True)
    description = models.TextField()
    ip_address  = models.GenericIPAddressField(null=True, blank=True)
    metadata    = models.JSONField(default=dict, blank=True)
    timestamp   = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name        = 'Audit Log'
        verbose_name_plural = 'Audit Logs'
        ordering            = ('-timestamp',)
        indexes = [
            models.Index(fields=['organization', 'timestamp']),
            models.Index(fields=['actor', 'timestamp']),
            models.Index(fields=['action']),
        ]

    def __str__(self):
        return f'[{self.action}] {self.actor} @ {self.timestamp:%Y-%m-%d %H:%M}'