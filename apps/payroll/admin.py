from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import PayrollUpload, SalaryDeduction

STATUS_COLORS = {
    'pending': '#f59e0b', 'processing': '#3b82f6', 'done': '#10b981',
    'failed': '#ef4444', 'partial': '#f97316', 'queued': '#6b7280',
    'success': '#10b981', 'skipped': '#8b5cf6',
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
