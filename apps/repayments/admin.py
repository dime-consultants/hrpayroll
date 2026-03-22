from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import RepaymentBatch, RepaymentRecord, IdempotencyKey

STATUS_COLORS = {
    'draft': '#6b7280', 'approved': '#3b82f6', 'dispatching': '#f59e0b',
    'complete': '#10b981', 'failed': '#ef4444', 'pending': '#f59e0b',
    'success': '#10b981', 'skipped': '#8b5cf6', 'claimed': '#f59e0b',
}


class RepaymentRecordInline(TabularInline):
    model = RepaymentRecord
    fields = ('phone_number', 'amount_requested', 'amount_sent', 'status',
              'attempt_number', 'lms_response_code', 'dispatched_at')
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True
    max_num = 50


@admin.register(RepaymentBatch)
class RepaymentBatchAdmin(ModelAdmin):
    list_display = (
        'id_short', 'organization', 'show_status_badge',
        'total_deductions', 'successful_count', 'failed_count',
        'total_amount', 'successful_amount', 'approved_by', 'date_created'
    )
    list_filter = ('status', 'organization')
    search_fields = ('organization__name', 'approved_by__username')
    readonly_fields = (
        'id', 'organization', 'upload', 'total_deductions', 'total_amount',
        'successful_count', 'failed_count', 'skipped_count', 'successful_amount',
        'approved_by', 'approved_at', 'celery_task_id', 'date_created', 'date_modified'
    )
    inlines = [RepaymentRecordInline]
    date_hierarchy = 'date_created'

    fieldsets = (
        ('Batch', {'fields': ('id', 'organization', 'upload', 'status')}),
        ('Totals', {
            'fields': ('total_deductions', 'total_amount',
                       'successful_count', 'successful_amount',
                       'failed_count', 'skipped_count')
        }),
        ('Approval', {'fields': ('approved_by', 'approved_at', 'celery_task_id')}),
        ('Timestamps', {'fields': ('date_created', 'date_modified'), 'classes': ('collapse',)}),
    )

    actions = ['approve_batches']

    @display(description='Batch ID')
    def id_short(self, obj):
        return str(obj.id)[:8] + '…'

    @display(description='Status')
    def show_status_badge(self, obj):
        color = STATUS_COLORS.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;'
            'border-radius:4px;font-size:11px">{}</span>',
            color, obj.get_status_display()
        )

    @admin.action(description='Approve selected batches and queue dispatch')
    def approve_batches(self, request, queryset):
        from apps.repayments.tasks import dispatch_repayment_batch
        from django.contrib import messages
        approved = 0
        for batch in queryset.filter(status=RepaymentBatch.STATUS_DRAFT):
            batch.approve(request.user)
            task = dispatch_repayment_batch.delay(str(batch.id))
            batch.celery_task_id = task.id
            batch.status = RepaymentBatch.STATUS_DISPATCHING
            batch.save(update_fields=['celery_task_id', 'status'])
            approved += 1
        self.message_user(request, f'{approved} batch(es) approved and queued.', messages.SUCCESS)


@admin.register(RepaymentRecord)
class RepaymentRecordAdmin(ModelAdmin):
    list_display = (
        'phone_number', 'organization', 'amount_requested', 'amount_sent',
        'show_status_badge', 'attempt_number', 'lms_response_code', 'dispatched_at'
    )
    list_filter = ('status', 'organization')
    search_fields = ('phone_number', 'lms_response_code')
    readonly_fields = [f.name for f in RepaymentRecord._meta.fields]
    date_hierarchy = 'dispatched_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @display(description='Status')
    def show_status_badge(self, obj):
        color = STATUS_COLORS.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;'
            'border-radius:4px;font-size:11px">{}</span>',
            color, obj.get_status_display()
        )


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(ModelAdmin):
    list_display = ('key_short', 'organization', 'show_status_badge',
                    'created_at', 'expires_at', 'is_expired_display')
    list_filter = ('status', 'organization')
    search_fields = ('key',)
    readonly_fields = ('key', 'organization', 'status', 'response_payload',
                       'created_at', 'expires_at')

    def has_add_permission(self, request):
        return False

    @display(description='Key')
    def key_short(self, obj):
        return obj.key[:24] + '…'

    @display(description='Status')
    def show_status_badge(self, obj):
        color = STATUS_COLORS.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;'
            'border-radius:4px;font-size:11px">{}</span>',
            color, obj.get_status_display()
        )

    @display(description='Expired', boolean=True)
    def is_expired_display(self, obj):
        return obj.is_expired
