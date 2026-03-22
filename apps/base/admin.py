# apps/base/admin.py
"""
Base admin mixins — shared read-only field sets and display helpers
so individual app admins stay thin.
"""
from unfold.admin import ModelAdmin


class BaseModelAdmin(ModelAdmin):
    """
    Adds date_created / date_modified as readonly to every registered model.
    Inherit from this instead of unfold.admin.ModelAdmin directly.
    """
    readonly_fields = ('id', 'date_created', 'date_modified')

    def get_readonly_fields(self, request, obj=None):
        base = super().get_readonly_fields(request, obj)
        extra = ('id', 'date_created', 'date_modified')
        return tuple(dict.fromkeys(list(base) + list(extra)))