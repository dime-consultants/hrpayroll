# apps/base/models.py
"""
Shared abstract base models — mirrors the pattern from the Dime LMS core.models.
All apps inherit from these so UUID pk, timestamps, and name/description
are never duplicated.
"""
import uuid
from functools import wraps

from django.db import models


# ─────────────────────────────────────────────────────────────
# Signal decorator
# ─────────────────────────────────────────────────────────────

def disable_for_loaddata(signal_handler):
    """Decorator that turns off signal handlers when loading fixture data."""
    @wraps(signal_handler)
    def wrapper(*args, **kwargs):
        if kwargs.get('raw'):
            return
        signal_handler(*args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────
# Abstract base models
# ─────────────────────────────────────────────────────────────

class BaseModel(models.Model):
    """
    Root abstract model.
    Provides: UUID primary key, date_modified, date_created.
    Mirrors LMS core.models.BaseModel exactly.
    """
    id = models.UUIDField(
        max_length=100,
        default=uuid.uuid4,
        unique=True,
        editable=False,
        primary_key=True,
    )
    date_modified = models.DateTimeField(auto_now=True)
    date_created = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class GenericBaseModel(BaseModel):
    """
    Extends BaseModel with name and description.
    Mirrors LMS core.models.GenericBaseModel exactly.
    Use this for any model that needs a human-readable name.
    """
    name = models.CharField(max_length=100)
    description = models.TextField(max_length=255, blank=True, null=True)

    class Meta:
        abstract = True
