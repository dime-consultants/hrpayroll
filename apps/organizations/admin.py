# apps/organizations/admin.py
from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import TabularInline
from unfold.decorators import display

from apps.base.admin import BaseModelAdmin
from .models import CheckoffOrganizationMirror, HRUser, AuditLog


class HRUserInline(TabularInline):
    model = HRUser
    fields = ('user', 'role', 'is_active')
    extra = 0
    show_change_link = True


@admin.register(CheckoffOrganizationMirror)
class CheckoffOrganizationMirrorAdmin(BaseModelAdmin):
    list_display = ('name', 'code', 'lms_id', 'email', 'show_status', 'date_created')
    list_filter = ('is_active',)
    search_fields = ('name', 'code', 'lms_id', 'email')
    inlines = [HRUserInline]

    fieldsets = (
        ('Organization Details', {'fields': ('name', 'code', 'lms_id', 'is_active')}),
        ('Contact', {'fields': ('email', 'phone_number', 'contact_name')}),
        ('Timestamps', {'fields': ('id', 'date_created', 'date_modified'), 'classes': ('collapse',)}),
    )

    @display(description='Status', boolean=True)
    def show_status(self, obj):
        return obj.is_active


@admin.register(HRUser)
class HRUserAdmin(BaseModelAdmin):
    list_display = ('get_full_name', 'get_email', 'organization', 'role', 'is_active', 'date_created')
    list_filter = ('role', 'is_active', 'organization')
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'organization__name')

    fieldsets = (
        ('User', {'fields': ('user', 'organization', 'role', 'is_active')}),
        ('Meta', {'fields': ('created_by', 'id', 'date_created', 'date_modified'), 'classes': ('collapse',)}),
    )

    @display(description='Full Name')
    def get_full_name(self, obj):
        return obj.user.full_name or obj.user.email

    @display(description='Email')
    def get_email(self, obj):
        return obj.user.email


@admin.register(AuditLog)
class AuditLogAdmin(BaseModelAdmin):
    list_display = ('timestamp', 'actor', 'organization', 'action', 'object_id', 'ip_address')
    list_filter = ('action', 'organization')
    search_fields = ('actor__email', 'description', 'object_id')
    readonly_fields = (
        'id', 'actor', 'organization', 'action', 'object_id',
        'description', 'ip_address', 'metadata', 'timestamp',
        'date_created', 'date_modified',
    )
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser