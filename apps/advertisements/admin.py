from django.contrib import admin
from unfold.admin import ModelAdmin

from apps.base.admin import BaseModelAdmin

from .models import Advertisement


@admin.register(Advertisement)
class AdvertisementAdmin(BaseModelAdmin, ModelAdmin):
    list_display = ("title", "starts_at", "location", "is_active", "date_modified")
    list_filter = ("is_active", "starts_at")
    search_fields = ("title", "description", "location")
