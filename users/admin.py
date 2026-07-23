from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


class CustomUserAdmin(UserAdmin):
    model = User

    list_display = ['email', 'first_name', 'last_name', 'is_staff', 'is_active']
    search_fields = ['email', 'first_name', 'last_name']
    ordering = ['email']

    # Fields shown on the "add user" form — no username, no groups/perms yet
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name', 'password1', 'password2'),
        }),
    )

    # Fields shown on the "edit user" form
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name')}),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Important dates', {'fields': ('last_login',)}),
    )

    filter_horizontal = ('groups', 'user_permissions')


admin.site.register(User, CustomUserAdmin)