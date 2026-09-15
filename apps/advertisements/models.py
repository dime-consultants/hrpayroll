from django.db import models

from apps.base.models import BaseModel


class Advertisement(BaseModel):
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="subposts",
    )
    title = models.CharField(max_length=160)
    description = models.TextField(max_length=500)
    image = models.ImageField(upload_to="advertisements/")
    starts_at = models.DateTimeField()
    location = models.CharField(max_length=180, blank=True)
    price_label = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("starts_at", "-date_created")

    def __str__(self):
        return self.title
