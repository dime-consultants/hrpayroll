from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from .managers import UserManager


class Organization(models.Model):
    name       = models.CharField(max_length=255)
    slug       = models.SlugField(unique=True)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class User(AbstractBaseUser, PermissionsMixin):
    email      = models.EmailField(unique=True, null=False, blank=False)
    first_name = models.CharField(max_length=50)
    last_name  = models.CharField(max_length=50)
    is_active  = models.BooleanField(default=True)
    is_staff   = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD  = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = "user"

    def __str__(self):
        return self.email

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class HRUser(models.Model):
    user           = models.OneToOneField(
                         settings.AUTH_USER_MODEL,  # ← string ref, not direct class
                         on_delete=models.CASCADE,
                         related_name="hr_user",
                     )
    organization   = models.ForeignKey(
                         Organization, null=True, blank=True,
                         on_delete=models.SET_NULL, related_name="hr_users"
                     )
    is_admin       = models.BooleanField(default=False)
    is_super_admin = models.BooleanField(default=False)
    can_upload     = models.BooleanField(default=False)
    is_active      = models.BooleanField(default=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "HR user"

    def __str__(self):
        return f"{self.user.email} — {self.role_label}"

    @property
    def role_label(self):
        if self.is_super_admin:
            return "Super Admin"
        if self.is_admin:
            return "Admin"
        return "User"