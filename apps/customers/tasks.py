"""
apps/customers/tasks.py

Two-step pipeline fired after a CustomerRegistration is approved in Django
Admin (mirrors apps/loans/tasks.py's parse → eligibility pipeline):

  1. register_borrower_task   — registers the borrower with the LMS
  2. upload_kyc_documents_task — bundles the KYC photos into one LMS call

Both consume the ErrorClassification that apps.api.lms_client.register_borrower /
upload_kyc_document derive internally, the same way apps/repayments/tasks.py
does: classification -> get_retry_countdown(...) -> self.retry(countdown=...),
or stop retrying outright for BUSINESS/AUTH classifications (or once retries
are exhausted).
"""
import logging

from celery import shared_task
from django.utils import timezone

from apps.api.error_handling import ErrorClassification, get_retry_countdown

log = logging.getLogger(__name__)

RETRYABLE = (ErrorClassification.NETWORK, ErrorClassification.TRANSIENT, ErrorClassification.UNKNOWN)


def _identity_type_to_document_type(identity_type_name: str) -> str:
    return {
        'National ID': 'NATIONAL_ID',
        'Passport':    'PASSPORT',
    }.get(identity_type_name, 'NATIONAL_ID')


# ─────────────────────────────────────────────────────────────
# Step 1 — Register borrower (fires after admin approval)
# ─────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def register_borrower_task(self, registration_id: str):
    from apps.customers.models import CustomerRegistration
    from apps.api.lms_client import register_borrower

    try:
        registration = CustomerRegistration.objects.select_related('organization').get(id=registration_id)
    except CustomerRegistration.DoesNotExist:
        log.error('register_borrower_task: registration %s not found', registration_id)
        return

    if registration.status not in (
        CustomerRegistration.STATUS_ACTIVE,
        CustomerRegistration.STATUS_PROCESSING,
    ):
        log.warning(
            'register_borrower_task: registration %s is %s — skipping (must be approved first)',
            registration_id, registration.status,
        )
        return

    registration.status = CustomerRegistration.STATUS_PROCESSING
    registration.save(update_fields=['status'])

    payload = {
        'first_name':            registration.first_name,
        'last_name':             registration.last_name,
        'other_name':            registration.other_name,
        'identity_number':       registration.identity_number,
        'phone_number':          registration.phone_number,
        # LMS wants DD/MM/YYYY here even though it returns ISO in the response.
        'date_of_birth':         registration.date_of_birth.strftime('%d/%m/%Y'),
        'address':               registration.address,
        'gender':                registration.gender,
        'email':                 registration.email,
        'working_status':        registration.working_status,
        'country':               registration.country,
        'salutation':            registration.salutation,
        'identity_type_name':    registration.identity_type_name,
        'borrower_type':         registration.borrower_type,
        'checkoff_organization': registration.organization.name,
    }

    body, classification = register_borrower(payload)
    registration.registration_response = body

    if classification is None:
        data = body.get('data', {})
        registration.lms_customer_id  = data.get('customer_id', '')
        registration.lms_loan_disk_id = data.get('loan_disk_id', '')
        registration.save(update_fields=['registration_response', 'lms_customer_id', 'lms_loan_disk_id'])

        log.info(
            'register_borrower_task: registration=%s registered lms_customer_id=%s',
            registration_id, registration.lms_customer_id,
        )
        upload_kyc_documents_task.delay(str(registration.id))
        return

    if classification in RETRYABLE:
        registration.save(update_fields=['registration_response'])
        if self.request.retries < self.max_retries:
            countdown = get_retry_countdown(self.request.retries + 1, classification)
            log.warning(
                'register_borrower_task: registration=%s failed (%s) — retrying in %ds',
                registration_id, classification.value, countdown,
            )
            raise self.retry(countdown=countdown)

    # BUSINESS/AUTH, or retries exhausted — no KYC upload without a registered borrower
    registration.status         = CustomerRegistration.STATUS_FAILED
    registration.failure_reason = body.get('message', 'Borrower registration failed.')
    registration.save(update_fields=['status', 'failure_reason', 'registration_response'])
    log.error(
        'register_borrower_task: registration=%s FAILED — %s',
        registration_id, registration.failure_reason,
    )


# ─────────────────────────────────────────────────────────────
# Step 2 — Upload KYC documents (fires after successful registration)
# ─────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def upload_kyc_documents_task(self, registration_id: str):
    from apps.customers.models import CustomerRegistration, KYCDocument
    from apps.api.lms_client import upload_kyc_document

    try:
        registration = CustomerRegistration.objects.get(id=registration_id)
    except CustomerRegistration.DoesNotExist:
        log.error('upload_kyc_documents_task: registration %s not found', registration_id)
        return

    if not registration.lms_customer_id:
        log.error(
            'upload_kyc_documents_task: registration %s has no lms_customer_id — borrower not registered yet',
            registration_id,
        )
        return

    documents = list(
        registration.kyc_documents.filter(
            status__in=[KYCDocument.STATUS_APPROVED, KYCDocument.STATUS_FAILED],
        )
    )
    if not documents:
        log.warning('upload_kyc_documents_task: no pending KYC documents for registration %s', registration_id)
        return

    # Bundled into ONE LMS call per the confirmed contract: document_type is
    # the borrower's primary identity document, front/back cover National ID
    # (Passport only has a single bio-page photo, sent as front_side_photo),
    # selfie is always included alongside for liveness.
    field_map = {
        KYCDocument.DOC_NATIONAL_ID_FRONT: 'front_side_photo',
        KYCDocument.DOC_NATIONAL_ID_BACK:  'back_side_photo',
        KYCDocument.DOC_PASSPORT:          'front_side_photo',
        KYCDocument.DOC_SELFIE:            'selfie_photo',
    }

    files = {}
    opened = []
    try:
        for doc in documents:
            doc.file.open('rb')
            opened.append(doc.file)
            files[field_map[doc.document_type]] = doc.file

        document_type = _identity_type_to_document_type(registration.identity_type_name)
        body, classification = upload_kyc_document(
            customer_id=registration.lms_customer_id,
            document_type=document_type,
            files=files,
        )
    finally:
        for f in opened:
            f.close()

    if classification is None:
        now = timezone.now()
        for doc in documents:
            doc.status       = KYCDocument.STATUS_UPLOADED
            doc.lms_response = body
            doc.uploaded_at  = now
            doc.save(update_fields=['status', 'lms_response', 'uploaded_at'])

        registration.status = CustomerRegistration.STATUS_DONE
        registration.save(update_fields=['status'])
        log.info('upload_kyc_documents_task: registration=%s all KYC documents uploaded', registration_id)
        return

    if classification in RETRYABLE and self.request.retries < self.max_retries:
        countdown = get_retry_countdown(self.request.retries + 1, classification)
        log.warning(
            'upload_kyc_documents_task: registration=%s failed (%s) — retrying in %ds',
            registration_id, classification.value, countdown,
        )
        raise self.retry(countdown=countdown)

    # BUSINESS/AUTH, or retries exhausted — borrower stays registered in the
    # LMS; KYC can be retried later via the admin action without re-registering.
    failure_reason = body.get('message', 'KYC document upload failed.')
    for doc in documents:
        doc.status         = KYCDocument.STATUS_FAILED
        doc.lms_response   = body
        doc.failure_reason = failure_reason
        doc.save(update_fields=['status', 'lms_response', 'failure_reason'])

    registration.status         = CustomerRegistration.STATUS_PARTIAL
    registration.failure_reason = failure_reason
    registration.save(update_fields=['status', 'failure_reason'])
    log.error('upload_kyc_documents_task: registration=%s KYC upload FAILED — %s', registration_id, failure_reason)
