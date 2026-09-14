"""
apps/loans/admin.py

Upload flow:
  HR uploads Excel → STATUS: approval_pending
  Admin approves upload → parse_loan_upload fires → rows created → eligibility checks run
  Admin approves batch → set_loan_limits_for_batch fires → loan limits set on LMS
  Customer self-serves via USSD or mobile app using their new limit.

Admin actions:
  LoanRequestUploadAdmin:
    - approve_uploads        → marks approved + fires parse_loan_upload
    - reject_uploads         → marks failed (pre-processing rejection)

  LoanRequestBatchAdmin:
    - approve_and_set_limits → approves batch + fires set_loan_limits_for_batch
    - recheck_eligibility    → resets ineligible/failed rows and reruns eligibility checks
    - refresh_batch_counters → manually refreshes success/fail counters

  LoanRequestAdmin:
    - retry_limit_set        → resets failed rows to eligible and retries set_loan_limits_for_batch
    - recheck_single_eligibility → reruns eligibility check for selected rows
    - mark_skipped           → manually marks as skipped
"""
import logging

from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from .models import LoanRequest, LoanRequestBatch, LoanRequestUpload

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Inline: requests inside upload detail
# ─────────────────────────────────────────────────────────────

class LoanRequestInline(TabularInline):
    model            = LoanRequest
    extra            = 0
    fields           = (
        'phone_number', 'employee_name', 'requested_amount',
        'guarantor_id_number', 'guarantor_phone_number',
        'existing_loan_balance', 'accessible_loan_limit',
        'status', 'ineligibility_reason',
    )
    readonly_fields  = fields
    can_delete       = False
    show_change_link = True
    max_num          = 0


# ─────────────────────────────────────────────────────────────
# Upload admin
# ─────────────────────────────────────────────────────────────

@admin.register(LoanRequestUpload)
class LoanRequestUploadAdmin(ModelAdmin):
    list_display = (
        'id_short', 'organization', 'loan_period', 'status_badge',
        'total_rows', 'eligible_rows', 'ineligible_rows', 'progress',
        'uploaded_by', 'approved_by', 'date_created',
    )
    list_filter   = ('status', 'organization', 'loan_period')
    search_fields = ('organization__name', 'organization__code', 'original_filename')
    readonly_fields = (
        'id', 'status', 'total_rows', 'processed_rows', 'failed_rows',
        'eligible_rows', 'ineligible_rows', 'error_log',
        'celery_task_id', 'approved_by', 'approved_at',
        'date_created', 'date_modified',
    )
    inlines       = [LoanRequestInline]
    date_hierarchy = 'date_created'
    ordering      = ('-date_created',)

    fieldsets = (
        ('Upload Details', {
            'fields': (
                'id', 'organization', 'uploaded_by', 'file', 'original_filename',
                'loan_period', 'notes', 'status',
            ),
        }),
        ('Processing', {
            'fields': (
                'total_rows', 'processed_rows', 'failed_rows',
                'eligible_rows', 'ineligible_rows',
                'error_log', 'celery_task_id',
            ),
        }),
        ('Approval', {
            'fields': ('approved_by', 'approved_at'),
        }),
        ('Timestamps', {
            'fields': ('date_created', 'date_modified'),
        }),
    )

    actions = ['approve_uploads', 'reject_uploads']

    @display(description='ID')
    def id_short(self, obj):
        return str(obj.id)[:8]

    @display(description='Status', ordering='status')
    def status_badge(self, obj):
        colours = {
            'approval_pending': '#f59e0b',
            'approved':         '#3b82f6',
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

    @display(description='Progress')
    def progress(self, obj):
        return f'{obj.progress_percent}%'

    @action(description='✅ Approve selected uploads and start processing')
    def approve_uploads(self, request, queryset):
        from .tasks import parse_loan_upload
        approved = 0
        skipped  = 0
        for upload in queryset:
            if upload.status != LoanRequestUpload.STATUS_APPROVAL_PENDING:
                skipped += 1
                continue
            upload.approve(request.user)
            parse_loan_upload.delay(str(upload.id))
            log.info('Admin %s approved loan upload %s', request.user, upload.id)
            approved += 1

        if approved:
            self.message_user(
                request,
                f'{approved} upload(s) approved and queued for processing.',
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f'{skipped} upload(s) skipped — only "Approval Pending" uploads can be approved.',
                messages.WARNING,
            )

    @action(description='❌ Reject selected uploads')
    def reject_uploads(self, request, queryset):
        updated = queryset.filter(
            status=LoanRequestUpload.STATUS_APPROVAL_PENDING,
        ).update(status=LoanRequestUpload.STATUS_FAILED)
        self.message_user(request, f'{updated} upload(s) rejected.', messages.WARNING)


# ─────────────────────────────────────────────────────────────
# Batch admin
# ─────────────────────────────────────────────────────────────

@admin.register(LoanRequestBatch)
class LoanRequestBatchAdmin(ModelAdmin):
    list_display = (
        'id_short', 'organization', 'status_badge',
        'total_requests', 'eligible_count', 'ineligible_count',
        'successful_count', 'failed_count',
        'total_amount', 'successful_amount',
        'approved_by', 'date_created',
    )
    list_filter   = ('status', 'organization')
    search_fields = ('organization__name', 'organization__code')
    readonly_fields = (
        'id', 'status', 'total_requests', 'total_amount',
        'eligible_count', 'ineligible_count',
        'successful_count', 'failed_count', 'skipped_count', 'successful_amount',
        'approved_by', 'approved_at', 'celery_task_id',
        'date_created', 'date_modified',
    )
    ordering = ('-date_created',)

    fieldsets = (
        ('Batch', {
            'fields': ('id', 'organization', 'upload', 'status'),
        }),
        ('Counters', {
            'fields': (
                'total_requests', 'total_amount',
                'eligible_count', 'ineligible_count',
                'successful_count', 'failed_count', 'skipped_count', 'successful_amount',
            ),
        }),
        ('Approval', {
            'fields': ('approved_by', 'approved_at', 'celery_task_id'),
        }),
        ('Timestamps', {
            'fields': ('date_created', 'date_modified'),
        }),
    )

    actions = ['approve_and_set_limits', 'recheck_eligibility', 'refresh_batch_counters']

    @display(description='ID')
    def id_short(self, obj):
        return str(obj.id)[:8]

    @display(description='Status', ordering='status')
    def status_badge(self, obj):
        colours = {
            'draft':       '#f59e0b',
            'approved':    '#3b82f6',
            'dispatching': '#8b5cf6',
            'complete':    '#10b981',
            'failed':      '#ef4444',
        }
        colour = colours.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:11px">{}</span>',
            colour, obj.get_status_display(),
        )

    @action(description='✅ Approve and set loan limits on LMS')
    def approve_and_set_limits(self, request, queryset):
        from .tasks import set_loan_limits_for_batch
        approved = 0
        skipped  = 0

        for batch in queryset:
            if batch.status not in (
                LoanRequestBatch.STATUS_DRAFT,
                LoanRequestBatch.STATUS_APPROVED,
            ):
                skipped += 1
                continue

            batch.status      = LoanRequestBatch.STATUS_APPROVED
            batch.approved_by = request.user
            batch.approved_at = timezone.now()
            batch.save(update_fields=['status', 'approved_by', 'approved_at'])

            set_loan_limits_for_batch.delay(str(batch.id))
            log.info(
                'Admin %s approved loan batch %s — set_loan_limits_for_batch queued',
                request.user, batch.id,
            )
            approved += 1

        if approved:
            self.message_user(
                request,
                f'{approved} batch(es) approved. Loan limits are being updated on the LMS. '
                f'Customers can now apply for loans via USSD or mobile app.',
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f'{skipped} batch(es) skipped — only Draft or Approved batches can be processed.',
                messages.WARNING,
            )

    @action(description='🔄 Re-run eligibility checks on ineligible/failed requests')
    def recheck_eligibility(self, request, queryset):
        from .tasks import check_loan_eligibility_batch
        total_reset = 0
        for batch in queryset:
            reset = LoanRequest.objects.filter(
                upload=batch.upload,
                status__in=[LoanRequest.STATUS_INELIGIBLE, LoanRequest.STATUS_FAILED],
            ).update(
                status=LoanRequest.STATUS_QUEUED,
                ineligibility_reason='',
            )
            total_reset += reset
            check_loan_eligibility_batch.delay(str(batch.upload_id))
            log.info(
                'Admin %s re-checking eligibility for batch %s (%d rows reset)',
                request.user, batch.id, reset,
            )

        self.message_user(
            request,
            f'Eligibility re-check queued for {queryset.count()} batch(es) '
            f'({total_reset} rows reset).',
            messages.SUCCESS,
        )

    @action(description='🔢 Refresh counters on selected batches')
    def refresh_batch_counters(self, request, queryset):
        for batch in queryset:
            batch.refresh_counters()
        self.message_user(
            request,
            f'{queryset.count()} batch(es) counters refreshed.',
            messages.SUCCESS,
        )


# ─────────────────────────────────────────────────────────────
# Loan request admin
# ─────────────────────────────────────────────────────────────

@admin.register(LoanRequest)
class LoanRequestAdmin(ModelAdmin):
    list_display = (
        'phone_number', 'employee_name', 'organization',
        'requested_amount', 'guarantor_id_number', 'guarantor_phone_number',
        'existing_loan_balance', 'new_limit',
        'status_badge', 'failure_reason', 'date_created',
    )
    list_filter   = ('status', 'organization', 'upload__loan_period')
    search_fields = (
        'phone_number', 'employee_name', 'employee_id',
        'guarantor_id_number', 'guarantor_phone_number',
    )
    readonly_fields = (
        'id', 'upload', 'organization', 'idempotency_key',
        'accessible_loan_limit', 'existing_loan_balance',
        'status', 'attempts', 'last_attempt_at', 'lms_response',
        'failure_reason', 'lms_loan_id', 'date_created', 'date_modified',
    )
    ordering      = ('-date_created',)
    date_hierarchy = 'date_created'

    fieldsets = (
        ('Request', {
            'fields': (
                'id', 'upload', 'organization',
                'phone_number', 'employee_name', 'employee_id',
                'requested_amount', 'reference', 'row_number',
                'guarantor_id_number', 'guarantor_phone_number',
            ),
        }),
        ('Eligibility & Limit', {
            'fields': (
                'existing_loan_balance', 'accessible_loan_limit',
                'ineligibility_reason',
            ),
        }),
        ('Result', {
            'fields': (
                'status', 'attempts', 'last_attempt_at',
                'failure_reason', 'lms_response', 'lms_loan_id',
                'idempotency_key',
            ),
        }),
        ('Timestamps', {
            'fields': ('date_created', 'date_modified'),
        }),
    )

    actions = ['retry_limit_set', 'recheck_single_eligibility', 'mark_skipped']

    @display(description='New Limit')
    def new_limit(self, obj):
        """Shows what the loan limit will be set to: existing_balance + requested_amount."""
        if obj.existing_loan_balance is not None:
            return obj.existing_loan_balance + obj.requested_amount
        return '—'

    @display(description='Status', ordering='status')
    def status_badge(self, obj):
        colours = {
            'queued':               '#6b7280',
            'eligibility_checking': '#8b5cf6',
            'eligible':             '#3b82f6',
            'ineligible':           '#f97316',
            'processing':           '#8b5cf6',
            'success':              '#10b981',
            'failed':               '#ef4444',
            'skipped':              '#9ca3af',
        }
        colour = colours.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:11px">{}</span>',
            colour, obj.get_status_display(),
        )

    @action(description='↩️ Retry loan limit set for failed requests')
    def retry_limit_set(self, request, queryset):
        from .tasks import set_loan_limits_for_batch

        failed_qs = queryset.filter(status=LoanRequest.STATUS_FAILED)
        if not failed_qs.exists():
            self.message_user(request, 'No failed requests selected.', messages.WARNING)
            return

        # Group by batch and retry per batch
        batch_ids = set()
        reset_count = 0
        for req in failed_qs:
            try:
                batch = req.upload.loan_request_batch
                batch_ids.add(str(batch.id))
                req.status         = LoanRequest.STATUS_ELIGIBLE
                req.failure_reason = ''
                req.save(update_fields=['status', 'failure_reason'])
                reset_count += 1
            except Exception:
                self.message_user(
                    request,
                    f'No batch found for {req.phone_number} — skipping.',
                    messages.WARNING,
                )

        for batch_id in batch_ids:
            set_loan_limits_for_batch.delay(batch_id)

        self.message_user(
            request,
            f'{reset_count} request(s) reset and loan limit update re-queued '
            f'for {len(batch_ids)} batch(es).',
            messages.SUCCESS,
        )

    @action(description='🔍 Re-check eligibility for selected requests')
    def recheck_single_eligibility(self, request, queryset):
        from .tasks import check_single_eligibility
        recheckable = queryset.filter(
            status__in=[
                LoanRequest.STATUS_INELIGIBLE,
                LoanRequest.STATUS_FAILED,
                LoanRequest.STATUS_QUEUED,
            ],
        )
        for req in recheckable:
            req.status               = LoanRequest.STATUS_QUEUED
            req.ineligibility_reason = ''
            req.save(update_fields=['status', 'ineligibility_reason'])
            check_single_eligibility.delay(str(req.id))

        self.message_user(
            request,
            f'{recheckable.count()} request(s) queued for eligibility re-check.',
            messages.SUCCESS,
        )

    @action(description='⏭️ Mark selected requests as skipped')
    def mark_skipped(self, request, queryset):
        updated = queryset.exclude(
            status=LoanRequest.STATUS_SUCCESS,
        ).update(status=LoanRequest.STATUS_SKIPPED)
        self.message_user(request, f'{updated} request(s) marked as skipped.', messages.WARNING)