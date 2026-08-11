"""
apps/customers/models.py

Tracks HR-initiated borrower onboarding (customer registration + KYC).

Flow:
  CustomerRegistration (approval_pending → processing → active [LMS code "200.001"] → done/partial/failed)
      └── KYCDocument (approval_pending → approved → uploaded/failed)  [one row per photo slot]

Key design decisions:
  - Registration is APPROVAL_PENDING on creation — admin must approve before
    Celery calls the LMS, mirroring apps.loans.
  - idempotency_key blocks accidental duplicate onboarding of the same person
    within an organisation (same identity_number + phone_number).
  - KYC documents are stored per-slot (national_id_front, national_id_back,
    passport, selfie) for admin review, but are bundled into a single LMS
    upload call at submission time — see apps/customers/tasks.py.
  - status=partial means the borrower was registered in the LMS successfully
    but the KYC upload call failed — recoverable via retry without
    re-registering the borrower.
"""
import hashlib

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

from apps.base.models import BaseModel
from apps.organizations.models import CheckoffOrganizationMirror


def kyc_document_path(instance, filename):
    org_code = instance.registration.organization.code
    return f'kyc/{org_code}/{timezone.now():%Y/%m}/{filename}'


# ─────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────

class CustomerRegistration(BaseModel):
    STATUS_APPROVAL_PENDING = 'approval_pending'
    STATUS_ACTIVE            = 'active'
    STATUS_PROCESSING       = 'processing'
    STATUS_DONE              = 'done'
    STATUS_PARTIAL           = 'partial'
    STATUS_FAILED            = 'failed'

    STATUS_CHOICES = [
        (STATUS_APPROVAL_PENDING, 'Approval Pending'),
        (STATUS_ACTIVE,           'Active'),
        (STATUS_PROCESSING,       'Processing'),
        (STATUS_DONE,             'Completed'),
        (STATUS_PARTIAL,          'Partially Processed'),
        (STATUS_FAILED,           'Failed'),
    ]

    GENDER_CHOICES = [
        ('Male',   'Male'),
        ('Female', 'Female'),
    ]

    IDENTITY_TYPE_CHOICES = [
        ('National ID', 'National ID'),
        ('Passport',    'Passport'),
    ]

    WORKING_STATUS_CHOICES = [
        ('Employee',      'Employee'),
        ('Self Employed', 'Self Employed'),
        ('Unemployed',    'Unemployed'),
    ]

    SALUTATION_CHOICES = [
        ('Mr',   'Mr'),
        ('Mrs',  'Mrs'),
        ('Ms',   'Ms'),
        ('Dr',   'Dr'),
        ('Prof', 'Prof'),
    ]

    organization    = models.ForeignKey(
        CheckoffOrganizationMirror, on_delete=models.PROTECT,
        related_name='customer_registrations',
    )
    submitted_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name='customer_registrations',
    )
    approved_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='approved_customer_registrations',
    )
    approved_at     = models.DateTimeField(null=True, blank=True)
    status          = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_APPROVAL_PENDING,
    )

    # Borrower personal info — maps 1:1 to the LMS register-borrower payload
    salutation          = models.CharField(max_length=10, choices=SALUTATION_CHOICES, blank=True)
    first_name          = models.CharField(max_length=100)
    last_name           = models.CharField(max_length=100)
    other_name          = models.CharField(max_length=100, blank=True)
    gender              = models.CharField(max_length=10, choices=GENDER_CHOICES)
    date_of_birth       = models.DateField()
    identity_type_name  = models.CharField(max_length=20, choices=IDENTITY_TYPE_CHOICES, default='National ID')
    identity_number     = models.CharField(max_length=50)
    phone_number        = models.CharField(max_length=20)
    email               = models.EmailField(blank=True)
    address             = models.CharField(max_length=255, blank=True)
    working_status      = models.CharField(max_length=20, choices=WORKING_STATUS_CHOICES, default='Employee')
    country             = models.CharField(max_length=5, default='KE')
    borrower_type       = models.CharField(max_length=20, default='Checkoff')

    # LMS response tracking
    lms_customer_id      = models.CharField(max_length=100, blank=True)
    lms_loan_disk_id     = models.CharField(max_length=100, blank=True)
    registration_response = models.JSONField(default=dict, blank=True)
    failure_reason        = models.TextField(blank=True)
    idempotency_key        = models.CharField(max_length=200, unique=True)
    notes                   = models.TextField(blank=True)

    class Meta:
        verbose_name        = 'Customer Registration'
        verbose_name_plural = 'Customer Registrations'
        ordering            = ('-date_created',)
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['phone_number']),
            models.Index(fields=['identity_number']),
        ]

    def __str__(self):
        return f'{self.first_name} {self.last_name} | {self.phone_number} | {self.status}'

    def build_idempotency_key(self):
        raw = f'customer:{self.organization_id}:{self.identity_number}:{self.phone_number}'
        return hashlib.sha256(raw.encode()).hexdigest()

    def save(self, *args, **kwargs):
        if not self.idempotency_key:
            self.idempotency_key = self.build_idempotency_key()
        super().save(*args, **kwargs)

    def approve(self, user):
        # Status stays approval_pending here — register_borrower_task moves it
        # to processing and only flips to active once the LMS confirms with
        # response code "200.001".
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save(update_fields=['approved_by', 'approved_at'])


# ─────────────────────────────────────────────────────────────
# KYC document
# ─────────────────────────────────────────────────────────────

class KYCDocument(BaseModel):
    DOC_NATIONAL_ID_FRONT = 'national_id_front'
    DOC_NATIONAL_ID_BACK  = 'national_id_back'
    DOC_PASSPORT          = 'passport'
    DOC_SELFIE            = 'selfie'

    DOCUMENT_TYPE_CHOICES = [
        (DOC_NATIONAL_ID_FRONT, 'National ID — Front'),
        (DOC_NATIONAL_ID_BACK,  'National ID — Back'),
        (DOC_PASSPORT,          'Passport'),
        (DOC_SELFIE,            'Selfie'),
    ]

    STATUS_APPROVAL_PENDING = 'approval_pending'
    STATUS_APPROVED         = 'approved'
    STATUS_UPLOADED         = 'uploaded'
    STATUS_FAILED           = 'failed'

    STATUS_CHOICES = [
        (STATUS_APPROVAL_PENDING, 'Approval Pending'),
        (STATUS_APPROVED,         'Approved'),
        (STATUS_UPLOADED,         'Uploaded'),
        (STATUS_FAILED,           'Failed'),
    ]

    registration    = models.ForeignKey(
        CustomerRegistration, on_delete=models.CASCADE, related_name='kyc_documents',
    )
    document_type   = models.CharField(max_length=20, choices=DOCUMENT_TYPE_CHOICES)
    file            = models.FileField(
        upload_to=kyc_document_path,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png'])],
    )
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_APPROVAL_PENDING)
    lms_response    = models.JSONField(default=dict, blank=True)
    failure_reason  = models.TextField(blank=True)
    uploaded_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'KYC Document'
        verbose_name_plural = 'KYC Documents'
        ordering            = ('document_type',)
        constraints = [
            models.UniqueConstraint(
                fields=['registration', 'document_type'],
                name='unique_document_type_per_registration',
            ),
        ]

    def __str__(self):
        return f'{self.registration_id} | {self.get_document_type_display()} | {self.status}'
