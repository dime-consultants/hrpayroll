"""
HR User Manager
===============
Manages creation and lifecycle of HRUser profiles linked to a
Custom AbstractBaseUser. Covers:

  - User + HRUser profile creation (regular, admin, super admin, superuser)
  - Role assignment  (is_admin, can_upload)
  - Organization membership (assign, transfer, remove)

Assumes the following model layout (adapt field names as needed):

    class User(AbstractBaseUser, PermissionsMixin):
        email        = models.EmailField(unique=True)
        is_active    = models.BooleanField(default=True)
        is_staff     = models.BooleanField(default=False)
        USERNAME_FIELD  = "email"
        REQUIRED_FIELDS = []
        objects = UserManager()

    class Organization(models.Model):
        name = models.CharField(max_length=255)

    class HRUser(models.Model):
        user         = models.OneToOneField(User, on_delete=models.CASCADE, related_name="hr_user")
        organization = models.ForeignKey(Organization, null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name="hr_users")
        is_admin     = models.BooleanField(default=False)
        can_upload   = models.BooleanField(default=False)
        is_active    = models.BooleanField(default=True)
        created_at   = models.DateTimeField(auto_now_add=True)
        updated_at   = models.DateTimeField(auto_now=True)
"""

import logging
from typing import Optional

from django.contrib.auth.base_user import BaseUserManager
from django.db import transaction

logger = logging.getLogger(__name__)


class UserManager(BaseUserManager):
    """
    Custom manager for the AbstractBaseUser-based User model.

    Extends BaseUserManager with HRUser profile creation, role
    assignment, and organization membership helpers.
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_hr_user_model(self):
        """Lazy import to avoid circular imports at module load time."""
        from .models import HRUser
        return HRUser

    def _get_organization_model(self):
        from .models import Organization
        return Organization

    def _create_user(
        self,
        email: str,
        password: str,
        *,
        first_name: str = "",
        last_name: str = "",
        is_staff: bool = False,
        is_superuser: bool = False,
        **extra_fields,
    ):
        """
        Core factory: creates and saves a User record.
        Called by the public create_* methods — not intended to be used directly.
        """
        if not email:
            raise ValueError("An email address is required.")

        email = self.normalize_email(email)
        user = self.model(
            email=email,
            first_name=first_name,
            last_name=last_name,
            is_staff=is_staff,
            is_superuser=is_superuser,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        logger.info("Created User pk=%s email=%s", user.pk, email)
        return user

    def _create_hr_profile(
        self,
        user,
        *,
        organization=None,
        is_admin: bool = False,
        can_upload: bool = False,
    ):
        """
        Creates the companion HRUser profile for a User instance.
        Must be called inside an atomic block.
        """
        HRUser = self._get_hr_user_model()
        hr_user = HRUser.objects.create(
            user=user,
            organization=organization,
            is_admin=is_admin,
            can_upload=can_upload,
        )
        logger.info(
            "Created HRUser pk=%s for User pk=%s (is_admin=%s, can_upload=%s)",
            hr_user.pk, user.pk, is_admin, can_upload,
        )
        return hr_user

    # ------------------------------------------------------------------
    # Public creation API
    # ------------------------------------------------------------------

    @transaction.atomic
    def create_user(
        self,
        email: str,
        password: str,
        *,
        first_name: str = "",
        last_name: str = "",
        organization=None,
        can_upload: bool = False,
        **extra_fields,
    ):
        """
        Create a standard (non-admin) HR user.

        Args:
            email:        Login email address.
            password:     Plain-text password (will be hashed).
            first_name:   User's first name.
            last_name:    User's last name.
            organization: Organization instance or None.
            can_upload:   Grant payroll-upload permission immediately.
            **extra_fields: Any extra fields forwarded to the User model.

        Returns:
            tuple[User, HRUser]
        """
        user = self._create_user(
            email, password,
            first_name=first_name, last_name=last_name,
            **extra_fields,
        )
        hr_user = self._create_hr_profile(
            user,
            organization=organization,
            is_admin=False,
            can_upload=can_upload,
        )
        return user, hr_user

    @transaction.atomic
    def create_hr_admin(
        self,
        email: str,
        password: str,
        *,
        first_name: str = "",
        last_name: str = "",
        organization=None,
        can_upload: bool = True,
        **extra_fields,
    ):
        """
        Create an HR admin user (is_admin=True, is_staff=True).

        Admins receive can_upload=True by default since they typically
        need full access. Pass can_upload=False to override.

        Returns:
            tuple[User, HRUser]
        """
        extra_fields.setdefault("is_staff", True)
        user = self._create_user(
            email, password,
            first_name=first_name, last_name=last_name,
            **extra_fields,
        )
        hr_user = self._create_hr_profile(
            user,
            organization=organization,
            is_admin=True,
            can_upload=can_upload,
        )
        return user, hr_user

    @transaction.atomic
    def create_superuser(
        self,
        email: str,
        password: str,
        first_name: str = "",
        last_name: str = "",
        **extra_fields,
    ):
        """
        Create a Django superuser with a matching HRUser profile.
        Superusers are automatically HR admins with full permissions.

        Returns:
            tuple[User, HRUser]
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if not extra_fields["is_staff"]:
            raise ValueError("Superuser must have is_staff=True.")
        if not extra_fields["is_superuser"]:
            raise ValueError("Superuser must have is_superuser=True.")

        user = self._create_user(
            email, password,
            first_name=first_name, last_name=last_name,
            **extra_fields,
        )
        hr_user = self._create_hr_profile(
            user,
            organization=None,   # superusers span all orgs
            is_admin=True,
            can_upload=True,
        )
        return user, hr_user

    @transaction.atomic
    def create_super_admin(
        self,
        email: str,
        password: str,
        *,
        first_name: str = "",
        last_name: str = "",
        organization=None,
        **extra_fields,
    ):
        """
        Create an HR Super Admin — the highest privilege level within
        the HR system itself, distinct from Django's superuser.

        A super admin has:
          - is_admin=True      → passes IsHRAdmin
          - can_upload=True    → passes CanUpload
          - is_staff=True      → Django admin panel access
          - is_superuser=False → NOT a Django superuser (scoped to HR)
          - is_super_admin=True → HR-level super flag for future checks

        Use this for the top-tier HR operator account that should be
        able to manage other HR admins and users within an organization,
        but without the God-mode Django superuser access.

        To create a true Django superuser, use create_superuser() instead.

        Args:
            email:        Login email address.
            password:     Plain-text password (will be hashed).
            organization: Organization instance this super admin belongs to.
                          Unlike create_superuser(), a super admin is always
                          scoped to an organization.
            **extra_fields: Any extra fields forwarded to the User model.

        Returns:
            tuple[User, HRUser]

        Raises:
            ValueError: If no organization is provided.

        Example:
            org = Organization.objects.get(slug="acme")
            user, hr_user = User.objects.create_super_admin(
                email="hr-lead@acme.com",
                password="s3cur3p@ss",
                organization=org,
            )
        """
        if organization is None:
            raise ValueError(
                "create_super_admin() requires an organization. "
                "Super admins are always scoped to an org. "
                "Use create_superuser() for org-less Django superusers."
            )

        extra_fields.setdefault("is_staff", True)
        extra_fields["is_superuser"] = False  # hard-enforce: not a Django superuser

        user = self._create_user(
            email, password,
            first_name=first_name, last_name=last_name,
            **extra_fields,
        )

        HRUser = self._get_hr_user_model()

        # Set is_super_admin if the model supports it, silently skip if not.
        # Add `is_super_admin = models.BooleanField(default=False)` to HRUser
        # to take full advantage of this flag.
        create_kwargs = dict(
            user=user,
            organization=organization,
            is_admin=True,
            can_upload=True,
        )
        if hasattr(HRUser, "is_super_admin"):
            create_kwargs["is_super_admin"] = True

        hr_user = HRUser.objects.create(**create_kwargs)

        logger.info(
            "Created HR Super Admin HRUser pk=%s for User pk=%s in Organization pk=%s",
            hr_user.pk, user.pk, organization.pk,
        )
        return user, hr_user

    # ------------------------------------------------------------------
    # Role assignment
    # ------------------------------------------------------------------

    @transaction.atomic
    def assign_roles(
        self,
        hr_user,
        *,
        is_admin: Optional[bool] = None,
        can_upload: Optional[bool] = None,
        is_super_admin: Optional[bool] = None,
    ):
        """
        Update role flags on an existing HRUser.

        Only the kwargs you pass will be changed; omitted ones are left alone.

        Args:
            hr_user:        HRUser instance to update.
            is_admin:       Set HR admin status, or None to leave unchanged.
            can_upload:     Set upload permission, or None to leave unchanged.
            is_super_admin: Promote/demote HR super admin status.
                            Promoting automatically forces is_admin=True and
                            can_upload=True. Ignored if HRUser has no such field.

        Returns:
            HRUser (refreshed from DB)

        Raises:
            ValueError: If no role kwargs are supplied.

        Example:
            manager.assign_roles(hr_user, is_admin=True, can_upload=True)
            manager.assign_roles(hr_user, is_super_admin=True)
        """
        updated_fields = []

        # Promoting to super admin implies full admin + upload rights.
        if is_super_admin is True:
            is_admin = True
            can_upload = True

        if is_super_admin is not None and hasattr(hr_user, "is_super_admin"):
            hr_user.is_super_admin = is_super_admin
            updated_fields.append("is_super_admin")

        if is_admin is not None:
            hr_user.is_admin = is_admin
            updated_fields.append("is_admin")

            # Mirror is_staff on the underlying User so Django admin
            # access stays consistent with HR admin status.
            hr_user.user.is_staff = is_admin
            hr_user.user.save(update_fields=["is_staff"])

        if can_upload is not None:
            hr_user.can_upload = can_upload
            updated_fields.append("can_upload")

        if not updated_fields:
            raise ValueError(
                "At least one of `is_admin`, `can_upload`, or `is_super_admin` must be provided."
            )

        updated_fields.append("updated_at")
        hr_user.save(update_fields=updated_fields)
        logger.info(
            "Updated roles for HRUser pk=%s — %s",
            hr_user.pk,
            {f: getattr(hr_user, f) for f in updated_fields if f != "updated_at"},
        )
        return hr_user

    def promote_to_admin(self, hr_user):
        """
        Shorthand: grant is_admin + can_upload to an HRUser.

        Returns:
            HRUser
        """
        return self.assign_roles(hr_user, is_admin=True, can_upload=True)

    def demote_from_admin(self, hr_user):
        """
        Shorthand: revoke is_admin (preserves can_upload).
        Also clears is_super_admin if the field exists.

        Returns:
            HRUser
        """
        kwargs: dict = {"is_admin": False}
        if hasattr(hr_user, "is_super_admin"):
            kwargs["is_super_admin"] = False
        return self.assign_roles(hr_user, **kwargs)

    def promote_to_super_admin(self, hr_user, organization=None):
        """
        Promote an existing HRUser to HR Super Admin.

        Optionally re-assigns them to a new organization at the same time.

        Returns:
            HRUser
        """
        if organization is not None:
            self.assign_organization(hr_user, organization)
        return self.assign_roles(hr_user, is_super_admin=True)

    def demote_from_super_admin(self, hr_user):
        """
        Strip super admin status while keeping is_admin and can_upload intact
        so the user retains ordinary HR admin access.

        Returns:
            HRUser
        """
        if not hasattr(hr_user, "is_super_admin"):
            logger.warning(
                "HRUser pk=%s has no is_super_admin field — no-op.", hr_user.pk
            )
            return hr_user
        return self.assign_roles(hr_user, is_super_admin=False)

    def grant_upload(self, hr_user):
        """Grant payroll-upload permission without touching admin status."""
        return self.assign_roles(hr_user, can_upload=True)

    def revoke_upload(self, hr_user):
        """Revoke payroll-upload permission without touching admin status."""
        return self.assign_roles(hr_user, can_upload=False)

    # ------------------------------------------------------------------
    # Organization membership
    # ------------------------------------------------------------------

    @transaction.atomic
    def assign_organization(self, hr_user, organization):
        """
        Assign (or re-assign) an HRUser to an Organization.

        If the user already belongs to a different organization, this
        performs a transfer — log the old org before overwriting.

        Args:
            hr_user:      HRUser instance.
            organization: Organization instance to assign.

        Returns:
            HRUser
        """
        if organization is None:
            raise ValueError(
                "organization cannot be None — use remove_from_organization() instead."
            )

        old_org = hr_user.organization
        if old_org and old_org.pk == organization.pk:
            logger.debug(
                "HRUser pk=%s is already in Organization pk=%s — no-op.",
                hr_user.pk, organization.pk,
            )
            return hr_user

        hr_user.organization = organization
        hr_user.save(update_fields=["organization", "updated_at"])
        logger.info(
            "HRUser pk=%s moved from Organization pk=%s → pk=%s",
            hr_user.pk,
            old_org.pk if old_org else None,
            organization.pk,
        )
        return hr_user

    @transaction.atomic
    def remove_from_organization(self, hr_user):
        """
        Detach an HRUser from their current Organization.

        Also revokes admin and upload roles, since those are inherently
        organization-scoped and would be meaningless without membership.

        Returns:
            HRUser
        """
        old_org = hr_user.organization
        hr_user.organization = None
        hr_user.is_admin = False
        hr_user.can_upload = False

        save_fields = ["organization", "is_admin", "can_upload", "updated_at"]
        if hasattr(hr_user, "is_super_admin"):
            hr_user.is_super_admin = False
            save_fields.append("is_super_admin")

        hr_user.save(update_fields=save_fields)

        # Keep Django's is_staff in sync
        hr_user.user.is_staff = False
        hr_user.user.save(update_fields=["is_staff"])

        logger.info(
            "HRUser pk=%s removed from Organization pk=%s; roles revoked.",
            hr_user.pk, old_org.pk if old_org else None,
        )
        return hr_user

    def get_organization_members(self, organization):
        """
        Return a QuerySet of all active HRUsers in a given Organization.

        Args:
            organization: Organization instance.

        Returns:
            QuerySet[HRUser]
        """
        HRUser = self._get_hr_user_model()
        return (
            HRUser.objects
            .filter(organization=organization, is_active=True)
            .select_related("user")
            .order_by("user__email")
        )

    def get_organization_admins(self, organization):
        """
        Return a QuerySet of all active HR admins in a given Organization.

        Returns:
            QuerySet[HRUser]
        """
        return self.get_organization_members(organization).filter(is_admin=True)

    # ------------------------------------------------------------------
    # Profile lifecycle
    # ------------------------------------------------------------------

    @transaction.atomic
    def deactivate(self, hr_user):
        """
        Soft-deactivate an HRUser and their underlying User account.

        Deactivated users will fail the IsHRUser / IsHRAdmin permission
        checks because request.user.is_authenticated will be False after
        their session expires, and is_active=False blocks new logins.

        Returns:
            HRUser
        """
        hr_user.is_active = False
        hr_user.save(update_fields=["is_active", "updated_at"])

        hr_user.user.is_active = False
        hr_user.user.save(update_fields=["is_active"])

        logger.info("Deactivated HRUser pk=%s / User pk=%s", hr_user.pk, hr_user.user.pk)
        return hr_user

    @transaction.atomic
    def reactivate(self, hr_user):
        """
        Re-activate a previously deactivated HRUser and their User account.

        Returns:
            HRUser
        """
        hr_user.is_active = True
        hr_user.save(update_fields=["is_active", "updated_at"])

        hr_user.user.is_active = True
        hr_user.user.save(update_fields=["is_active"])

        logger.info("Reactivated HRUser pk=%s / User pk=%s", hr_user.pk, hr_user.user.pk)
        return hr_user

    @transaction.atomic
    def update_profile(self, hr_user, **fields):
        """
        Update arbitrary HRUser fields in one call.

        Only fields that exist on HRUser are accepted; unknown keys raise
        an AttributeError so typos surface immediately.

        Args:
            hr_user:  HRUser instance.
            **fields: Keyword arguments mapping field name → new value.

        Returns:
            HRUser

        Example:
            manager.update_profile(hr_user, can_upload=True)
        """
        if not fields:
            raise ValueError("No fields provided to update_profile().")

        for key, value in fields.items():
            if not hasattr(hr_user, key):
                raise AttributeError(
                    f"HRUser has no field '{key}'. Check for typos."
                )
            setattr(hr_user, key, value)

        hr_user.save(update_fields=[*fields.keys(), "updated_at"])
        logger.info("Updated HRUser pk=%s fields=%s", hr_user.pk, list(fields.keys()))
        return hr_user