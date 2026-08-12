"""
apps/customers/admin.py

Flow:
  HR submits borrower + KYC docs → STATUS: approval_pending
  Admin approves registration → register_borrower_task fires → status becomes
    active once the LMS confirms with response code "200.001" →
    upload_kyc_documents_task fires → KYC uploaded
  Customer is now a fully onboarded LMS borrower.

Admin actions:
  CustomerRegistrationAdmin:
    - approve_registrations         → stamps approved_by/at (+ its KYC docs approved) +
                                        fires register_borrower_task; status stays
                                        approval_pending until the LMS confirms
    - reject_registrations          → marks failed (pre-processing rejection)
    - mark_as_failed                → force-marks selected registrations as failed regardless
                                        of current status (manual override, e.g. a stuck
                                        "processing" registration) — fires no LMS calls
    - reprocess_registrations        → resets any registration that isn't already active/done
                                        back to approval_pending (re-approving its KYC docs),
                                        unless the LMS already shows the borrower as active
                                        (skipped); does NOT fire register_borrower_task itself —
                                        use approve_registrations afterwards to actually retry
    - retry_kyc_upload              → re-fires upload_kyc_documents_task for partial/failed
                                        registrations without re-registering the borrower
"""
import logging

from django.contrib import admin, messages
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from .models import CustomerRegistration, KYCDocument

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Inline: KYC documents inside registration detail
# ─────────────────────────────────────────────────────────────

class KYCDocumentInline(TabularInline):
    model            = KYCDocument
    extra            = 0
    fields           = ('document_type', 'file_link', 'status', 'failure_reason')
    readonly_fields  = ('document_type', 'file_link', 'status', 'failure_reason')
    can_delete       = False
    max_num          = 0

    @display(description='File')
    def file_link(self, obj):
        if not obj.file:
            return '—'
        return format_html('<a href="{}" target="_blank">View document</a>', obj.file.url)


# ─────────────────────────────────────────────────────────────
# Registration admin
# ─────────────────────────────────────────────────────────────

@admin.register(CustomerRegistration)
class CustomerRegistrationAdmin(ModelAdmin):
    list_display = (
        'id_short', 'full_name', 'organization', 'phone_number',
        'identity_type_name', 'identity_number', 'status_badge',
        'submitted_by', 'approved_by', 'date_created',
    )
    list_filter   = ('status', 'organization', 'identity_type_name', 'gender')
    search_fields = ('first_name', 'last_name', 'phone_number', 'identity_number', 'organization__name')
    readonly_fields = (
        'id', 'status', 'lms_customer_id', 'lms_loan_disk_id',
        'registration_response', 'failure_reason', 'idempotency_key',
        'approved_by', 'approved_at', 'date_created', 'date_modified',
    )
    inlines       = [KYCDocumentInline]
    date_hierarchy = 'date_created'
    ordering      = ('-date_created',)

    fieldsets = (
        ('Borrower', {
            'fields': (
                'id', 'organization', 'submitted_by',
                'salutation', 'first_name', 'last_name', 'other_name',
                'gender', 'date_of_birth',
                'identity_type_name', 'identity_number',
                'phone_number', 'email', 'address',
                'working_status', 'country', 'borrower_type', 'notes',
            ),
        }),
        ('Status', {
            'fields': ('status', 'lms_customer_id', 'lms_loan_disk_id', 'failure_reason'),
        }),
        ('LMS Response', {
            'fields': ('registration_response', 'idempotency_key'),
            'classes': ('collapse',),
        }),
        ('Approval', {
            'fields': ('approved_by', 'approved_at'),
        }),
        ('Timestamps', {
            'fields': ('date_created', 'date_modified'),
        }),
    )

    actions = [
        'approve_registrations', 'reject_registrations', 'mark_as_failed',
        'reprocess_registrations', 'retry_kyc_upload',
    ]

    @display(description='ID')
    def id_short(self, obj):
        return str(obj.id)[:8]

    @display(description='Name')
    def full_name(self, obj):
        return f'{obj.first_name} {obj.last_name}'

    @display(description='Status', ordering='status')
    def status_badge(self, obj):
        colours = {
            'approval_pending': '#f59e0b',
            'active':           '#3b82f6',
            'processing':       '#8b5cf6',
            'done':             '#10b981',
            'partial':          '#f97316',
            'failed':           '#ef4444',
        }
        colour = colours.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:11px">{}</span>',
            colour, obj.get_status_display(),
        )

    @action(description='✅ Approve selected registrations and register with LMS')
    def approve_registrations(self, request, queryset):
        from .tasks import register_borrower_task
        approved = 0
        skipped  = 0
        for registration in queryset:
            if registration.status != CustomerRegistration.STATUS_APPROVAL_PENDING:
                skipped += 1
                continue
            registration.approve(request.user)
            registration.kyc_documents.update(status=KYCDocument.STATUS_APPROVED)
            register_borrower_task.delay(str(registration.id))
            log.info('Admin %s approved customer registration %s', request.user, registration.id)
            approved += 1

        if approved:
            self.message_user(
                request,
                f'{approved} registration(s) approved and queued for LMS registration.',
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f'{skipped} registration(s) skipped — only "Approval Pending" registrations can be approved.',
                messages.WARNING,
            )

    @action(description='❌ Reject selected registrations')
    def reject_registrations(self, request, queryset):
        updated = queryset.filter(
            status=CustomerRegistration.STATUS_APPROVAL_PENDING,
        ).update(status=CustomerRegistration.STATUS_FAILED)
        self.message_user(request, f'{updated} registration(s) rejected.', messages.WARNING)

    # @action(description='🚫 Mark selected registrations as failed')
    # def mark_as_failed(self, request, queryset):
    #     updated = 0
    #     skipped = 0
    #     for registration in queryset:
    #         if registration.status == CustomerRegistration.STATUS_FAILED:
    #             skipped += 1
    #             continue
    #         registration.status         = CustomerRegistration.STATUS_FAILED
    #         registration.failure_reason = f'Manually marked as failed by {request.user}.'
    #         registration.save(update_fields=['status', 'failure_reason'])
    #         log.info('Admin %s manually marked customer registration %s as failed', request.user, registration.id)
    #         updated += 1

    #     if updated:
    #         self.message_user(request, f'{updated} registration(s) marked as failed.', messages.WARNING)
    #     if skipped:
    #         self.message_user(
    #             request,
    #             f'{skipped} registration(s) skipped — already failed.',
    #             messages.WARNING,
    #         )
            
    @action(description='🔁 Reprocess registrations (reset anything not yet completed back to approval pending)')
    def reprocess_registrations(self, request, queryset):
        from apps.api.lms_client import get_customer_exclusive

        lms_statuses = {}
        for registration in queryset:
            if not registration.lms_customer_id:
                continue
            result = get_customer_exclusive(registration.lms_customer_id)
            lms_statuses[registration.id] = result.get('status') if result else None

        updated = 0
        skipped = 0

        for registration in queryset:
            if registration.status in (
                CustomerRegistration.STATUS_ACTIVE,
                CustomerRegistration.STATUS_DONE,
            ):
                skipped += 1
                continue

            lms_status = lms_statuses.get(registration.id)
            if lms_status:
                registration.status = lms_status
            else:
                registration.status = CustomerRegistration.STATUS_APPROVAL_PENDING
                registration.kyc_documents.update(status=KYCDocument.STATUS_APPROVED)

            registration.save(update_fields=['status'])
            log.info('Admin %s reprocessed customer registration %s', request.user, registration.id)
            updated += 1

        if updated:
            self.message_user(
                request,
                f'{updated} registration(s) reset. Use "Approve" to retry with the LMS.',
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f'{skipped} registration(s) skipped — already active or completed.',
                messages.WARNING,
            )
                



    @action(description='🔄 Retry KYC upload for partial/failed registrations')
    def retry_kyc_upload(self, request, queryset):
        from .tasks import upload_kyc_documents_task
        retried = 0
        skipped = 0
        for registration in queryset:
            if not registration.lms_customer_id:
                skipped += 1
                continue
            registration.kyc_documents.filter(
                status=KYCDocument.STATUS_FAILED,
            ).update(status=KYCDocument.STATUS_APPROVED, failure_reason='')
            upload_kyc_documents_task.delay(str(registration.id))
            retried += 1

        if retried:
            self.message_user(request, f'{retried} registration(s) queued for KYC retry.', messages.SUCCESS)
        if skipped:
            self.message_user(
                request,
                f'{skipped} registration(s) skipped — borrower not registered with the LMS yet.',
                messages.WARNING,
            )
