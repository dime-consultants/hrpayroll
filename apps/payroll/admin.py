from django.contrib import admin
from django.contrib import messages
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import PayrollUpload, SalaryDeduction

STATUS_COLORS = {
    'pending': '#f59e0b', 'processing': '#3b82f6', 'done': '#10b981',
    'failed': '#ef4444', 'partial': '#f97316', 'queued': '#6b7280',
    'success': '#10b981', 'skipped': '#8b5cf6',
}

# Statuses eligible for reprocessing.
# DONE and PARTIAL are excluded — they completed successfully.
# If you need to force-rerun a PARTIAL, manually flip its status to FAILED first.
REPROCESSABLE_STATUSES = {
    PayrollUpload.STATUS_PROCESSING,  # worker died mid-run
    PayrollUpload.STATUS_FAILED,      # hard exception
    PayrollUpload.STATUS_PENDING,     # never picked up
}

# Uploads land here straight from the UI and wait for an admin to kick off
# processing via the "Process selected uploads" action below.
PROCESSABLE_STATUSES = {
    PayrollUpload.STATUS_APPROVAL_PENDING,
}


class SalaryDeductionInline(TabularInline):
    model = SalaryDeduction
    fields = ('phone_number', 'employee_name', 'amount', 'status', 'failure_reason')
    readonly_fields = ('phone_number', 'employee_name', 'amount', 'status', 'failure_reason')
    extra = 0
    can_delete = False
    show_change_link = True
    max_num = 20


@admin.register(PayrollUpload)
class PayrollUploadAdmin(ModelAdmin):
    list_display = (
        'organization', 'payroll_period', 'original_filename',
        'show_status_badge', 'total_rows', 'success_rows_display',
        'failed_rows', 'show_progress', 'uploaded_by', 'date_created'
    )
    list_filter = ('status', 'organization', 'payroll_period')
    search_fields = ('organization__name', 'original_filename', 'uploaded_by__username')
    readonly_fields = (
        'id', 'status', 'total_rows', 'processed_rows', 'failed_rows',
        'error_log', 'celery_task_id', 'date_created', 'date_modified'
    )
    date_hierarchy = 'date_created'
    inlines = [SalaryDeductionInline]
    actions = ['process_uploads', 'reprocess_uploads']

    fieldsets = (
        ('Upload Details', {'fields': ('organization', 'payroll_period', 'file', 'notes')}),
        ('Processing', {
            'fields': ('status', 'total_rows', 'processed_rows', 'failed_rows',
                       'error_log', 'celery_task_id'),
            'classes': ('collapse',)
        }),
        ('Meta', {
            'fields': ('id', 'uploaded_by', 'date_created', 'date_modified'),
            'classes': ('collapse',)
        }),
    )

    @display(description='Status')
    def show_status_badge(self, obj):
        color = STATUS_COLORS.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;'
            'border-radius:4px;font-size:11px">{}</span>',
            color, obj.get_status_display()
        )

    @display(description='Progress')
    def show_progress(self, obj):
        pct = obj.progress_percent
        return format_html(
            '<div style="width:100px;background:#e5e7eb;border-radius:4px">'
            '<div style="width:{pct}%;background:#10b981;border-radius:4px;height:8px"></div>'
            '</div> {pct}%',
            pct=pct
        )

    @display(description='Successes')
    def success_rows_display(self, obj):
        return obj.success_rows

    @admin.action(description='▶ Process selected uploads')
    def process_uploads(self, request, queryset):
        from apps.repayments.tasks import parse_payroll_upload

        queued, skipped = [], []

        for upload in queryset:
            if upload.status not in PROCESSABLE_STATUSES:
                skipped.append(f'#{str(upload.id)[:8]} ({upload.get_status_display()})')
                continue

            task = parse_payroll_upload.delay(str(upload.id))

            upload.celery_task_id = task.id
            upload.save(update_fields=['celery_task_id'])

            queued.append(f'#{str(upload.id)[:8]}')

        if queued:
            self.message_user(
                request,
                f'Queued processing for {len(queued)} upload(s): {", ".join(queued)}.',
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f'Skipped {len(skipped)} upload(s) not awaiting approval: {", ".join(skipped)}.',
                messages.WARNING,
            )

    @admin.action(description='↺ Reprocess selected uploads (resets stuck / failed)')
    def reprocess_uploads(self, request, queryset):
        from apps.repayments.tasks import parse_payroll_upload

        queued, skipped = [], []

        for upload in queryset:
            if upload.status not in REPROCESSABLE_STATUSES:
                skipped.append(f'#{str(upload.id)[:8]} ({upload.get_status_display()})')
                continue

            # Reset state so parse_payroll_upload won't skip it.
            # Existing SalaryDeduction rows are intentionally kept —
            # bulk_create(ignore_conflicts=True) makes the parse step
            # idempotent, so only genuinely missing rows are inserted.
            upload.status = PayrollUpload.STATUS_PENDING
            upload.celery_task_id = ''
            upload.error_log = []
            upload.save(update_fields=['status', 'celery_task_id', 'error_log'])

            task = parse_payroll_upload.delay(str(upload.id))

            upload.celery_task_id = task.id
            upload.save(update_fields=['celery_task_id'])

            queued.append(f'#{str(upload.id)[:8]}')

        if queued:
            self.message_user(
                request,
                f'Queued reprocessing for {len(queued)} upload(s): {", ".join(queued)}.',
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f'Skipped {len(skipped)} upload(s) that already completed (DONE / PARTIAL). '
                f'To force a re-run, manually set the status to FAILED first: {", ".join(skipped)}.',
                messages.WARNING,
            )


@admin.register(SalaryDeduction)
class SalaryDeductionAdmin(ModelAdmin):
    list_display = (
        'phone_number', 'employee_name', 'organization', 'amount',
        'deduction_date', 'show_status_badge', 'attempts', 'last_attempt_at'
    )
    list_filter = ('status', 'organization', 'deduction_date')
    search_fields = ('phone_number', 'employee_name', 'employee_id', 'reference')
    readonly_fields = (
        'id', 'idempotency_key', 'attempts', 'last_attempt_at',
        'lms_response', 'date_created', 'date_modified'
    )
    date_hierarchy = 'deduction_date'

    @display(description='Status')
    def show_status_badge(self, obj):
        color = STATUS_COLORS.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;'
            'border-radius:4px;font-size:11px">{}</span>',
            color, obj.get_status_display()
        )