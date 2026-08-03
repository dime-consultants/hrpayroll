"""
apps/loans/admin.py

Admin actions:
  LoanRequestUploadAdmin:
    - approve_uploads          → marks approved + fires parse_loan_upload task
    - reject_uploads           → marks failed (pre-approval rejection)

  LoanRequestBatchAdmin:
    - approve_and_dispatch     → approves batch + fires dispatch_loan_batch task
    - recheck_eligibility      → re-runs eligibility checks on failed/ineligible rows

  LoanRequestAdmin:
    - retry_failed_requests    → re-queues failed requests and fires dispatch
    - mark_skipped             → manually marks as skipped
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
# Inline: requests inside upload
# ─────────────────────────────────────────────────────────────

class LoanRequestInline(TabularInline):
    model  = LoanRequest
    extra  = 0
    fields = (
        'phone_number', 'employee_name', 'requested_amount',
        'accessible_loan_limit', 'status', 'ineligibility_reason',
    )
    readonly_fields = fields
    can_delete      = False
    show_change_link = True
    max_num         = 0  # no "add" row


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
    list_filter  = ('status', 'organization', 'loan_period')
    search_fields = ('organization__name', 'organization__code', 'original_filename')
    readonly_fields = (
        'id', 'status', 'total_rows', 'processed_rows', 'failed_rows',
        'eligible_rows', 'ineligible_rows', 'error_log',
        'celery_task_id', 'approved_by', 'approved_at', 'date_created', 'date_modified',
    )
    inlines = [LoanRequestInline]
    date_hierarchy = 'date_created'
    ordering = ('-date_created',)

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

    # ── Display helpers ──────────────────────────────────────

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
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px">{}</span>',
            colour, obj.get_status_display(),
        )

    @display(description='Progress')
    def progress(self, obj):
        return f'{obj.progress_percent}%'

    # ── Admin actions ────────────────────────────────────────

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
        'successful_count', 'failed_count', 'skipped_count',
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

    actions = ['approve_and_dispatch', 'recheck_eligibility', 'refresh_batch_counters']

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
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px">{}</span>',
            colour, obj.get_status_display(),
        )


    @action(description='🚀 Approve and dispatch selected batches to LMS')
    def approve_and_dispatch(self, request, queryset):
        from .tasks import set_loan_limits_for_batch   # ← changed from dispatch_loan_batch

        dispatched = 0
        skipped    = 0

        for batch in queryset:
            if batch.status not in (LoanRequestBatch.STATUS_DRAFT, LoanRequestBatch.STATUS_APPROVED):
                skipped += 1
                continue

            batch.status      = LoanRequestBatch.STATUS_APPROVED
            batch.approved_by = request.user
            batch.approved_at = timezone.now()
            batch.save(update_fields=['status', 'approved_by', 'approved_at'])

            # set_loan_limits_for_batch → dispatch_loan_batch → dispatch_single_loan × N
            set_loan_limits_for_batch.delay(str(batch.id))

            log.info(
                'Admin %s approved loan batch %s — set_loan_limits_for_batch queued',
                request.user, batch.id,
            )
            dispatched += 1

        if dispatched:
            self.message_user(
                request,
                f'{dispatched} batch(es) approved. Loan limits are being set on the LMS — '
                f'loan disbursement will follow automatically.',
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f'{skipped} batch(es) skipped — only Draft or Approved batches can be dispatched.',
                messages.WARNING,
            )

    @action(description='🔄 Re-run eligibility checks on ineligible/failed requests')
    def recheck_eligibility(self, request, queryset):
        from .tasks import check_loan_eligibility_batch
        for batch in queryset:
            # Reset ineligible/failed rows back to queued so the check re-runs
            updated = LoanRequest.objects.filter(
                upload=batch.upload,
                status__in=[LoanRequest.STATUS_INELIGIBLE, LoanRequest.STATUS_FAILED],
            ).update(
                status=LoanRequest.STATUS_QUEUED,
                ineligibility_reason='',
            )
            check_loan_eligibility_batch.delay(str(batch.upload_id))
            log.info('Admin %s re-checking eligibility for batch %s (%d rows reset)', request.user, batch.id, updated)

        self.message_user(
            request,
            f'Eligibility re-check queued for {queryset.count()} batch(es).',
            messages.SUCCESS,
        )

    @action(description='🔢 Refresh counters on selected batches')
    def refresh_batch_counters(self, request, queryset):
        for batch in queryset:
            batch.refresh_counters()
        self.message_user(request, f'{queryset.count()} batch(es) counters refreshed.', messages.SUCCESS)


# ─────────────────────────────────────────────────────────────
# Loan request admin
# ─────────────────────────────────────────────────────────────

@admin.register(LoanRequest)
class LoanRequestAdmin(ModelAdmin):
    list_display = (
        'phone_number', 'employee_name', 'organization',
        'requested_amount', 'accessible_loan_limit', 'existing_loan_balance',
        'status_badge', 'attempts', 'lms_loan_id', 'date_created',
    )
    list_filter   = ('status', 'organization', 'upload__loan_period')
    search_fields = ('phone_number', 'employee_name', 'employee_id', 'lms_loan_id')
    readonly_fields = (
        'id', 'upload', 'organization', 'idempotency_key',
        'loan_limit', 'accessible_loan_limit', 'existing_loan_balance',
        'status', 'attempts', 'last_attempt_at', 'lms_response',
        'failure_reason', 'lms_loan_id', 'date_created', 'date_modified',
    )
    ordering = ('-date_created',)
    date_hierarchy = 'date_created'

    fieldsets = (
        ('Request', {
            'fields': (
                'id', 'upload', 'organization',
                'phone_number', 'employee_name', 'employee_id',
                'requested_amount', 'reference', 'row_number',
            ),
        }),
        ('Eligibility', {
            'fields': (
                'loan_limit', 'accessible_loan_limit', 'existing_loan_balance',
                'ineligibility_reason',
            ),
        }),
        ('Dispatch', {
            'fields': (
                'status', 'attempts', 'last_attempt_at',
                'lms_loan_id', 'failure_reason', 'lms_response',
                'idempotency_key',
            ),
        }),
        ('Timestamps', {
            'fields': ('date_created', 'date_modified'),
        }),
    )

    actions = ['retry_failed_requests', 'mark_skipped', 'recheck_single_eligibility']

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
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px">{}</span>',
            colour, obj.get_status_display(),
        )

    @action(description='↩️ Retry failed requests')
    def retry_failed_requests(self, request, queryset):
        from .tasks import dispatch_single_loan

        eligible = queryset.filter(status=LoanRequest.STATUS_FAILED)
        count    = 0
        batch_ids = set()

        for req in eligible:
            try:
                batch = req.upload.loan_request_batch
                batch_ids.add(str(batch.id))
            except LoanRequest.upload.related.related_model.loan_request_batch.RelatedObjectDoesNotExist:
                self.message_user(
                    request,
                    f'No batch found for request {req.phone_number} — skipping.',
                    messages.WARNING,
                )
                continue

            req.status = LoanRequest.STATUS_ELIGIBLE
            req.failure_reason = ''
            req.save(update_fields=['status', 'failure_reason'])
            dispatch_single_loan.delay(str(req.id), str(batch.id))
            count += 1

        self.message_user(request, f'{count} request(s) queued for retry.', messages.SUCCESS)

    @action(description='⏭️ Mark selected requests as skipped')
    def mark_skipped(self, request, queryset):
        updated = queryset.exclude(
            status=LoanRequest.STATUS_SUCCESS,
        ).update(status=LoanRequest.STATUS_SKIPPED)
        self.message_user(request, f'{updated} request(s) marked as skipped.', messages.WARNING)

    @action(description='🔍 Re-check eligibility for selected requests')
    def recheck_single_eligibility(self, request, queryset):
        from .tasks import check_single_eligibility
        recheckable = queryset.filter(
            status__in=[LoanRequest.STATUS_INELIGIBLE, LoanRequest.STATUS_FAILED, LoanRequest.STATUS_QUEUED],
        )
        for req in recheckable:
            req.status = LoanRequest.STATUS_QUEUED
            req.ineligibility_reason = ''
            req.save(update_fields=['status', 'ineligibility_reason'])
            check_single_eligibility.delay(str(req.id))

        self.message_user(
            request,
            f'{recheckable.count()} request(s) queued for eligibility re-check.',
            messages.SUCCESS,
        )