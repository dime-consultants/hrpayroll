from .models import User
from rest_framework import serializers

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'is_active', 'is_staff']
        read_only_fields = ['id', 'is_active', 'is_staff']

class UserSignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=50)
    last_name = serializers.CharField(max_length=50)

class UserSigninSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


"""
HR User Management — Serializers
==================================
Covers:
  - Organization CRUD
  - User registration (regular, admin, super admin)
  - Login / token response
  - HRUser profile read + update
  - Role assignment
  - Password change / reset
"""

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import HRUser, Organization, User


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------

class OrganizationSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()

    class Meta:
        model  = Organization
        fields = [
            "id", "name", "slug", "is_active",
            "member_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "member_count"]

    def get_member_count(self, obj) -> int:
        return obj.hr_users.filter(is_active=True).count()


class OrganizationWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Organization
        fields = ["name", "slug", "is_active"]

    def validate_slug(self, value: str) -> str:
        qs = Organization.objects.filter(slug=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("An organization with this slug already exists.")
        return value


# ---------------------------------------------------------------------------
# User (base read)
# ---------------------------------------------------------------------------

class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model  = User
        fields = [
            "id", "email", "first_name", "last_name",
            "full_name", "is_active", "is_staff",
        ]
        read_only_fields = ["id", "full_name", "is_active", "is_staff"]


# ---------------------------------------------------------------------------
# HRUser profile (nested read)
# ---------------------------------------------------------------------------

class HRUserSerializer(serializers.ModelSerializer):
    user         = UserSerializer(read_only=True)
    organization = OrganizationSerializer(read_only=True)
    role_label   = serializers.CharField(read_only=True)

    class Meta:
        model  = HRUser
        fields = [
            "id", "user", "organization", "role_label",
            "is_admin", "is_super_admin", "can_upload",
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Registration serializers
# ---------------------------------------------------------------------------

class RegisterUserSerializer(serializers.Serializer):
    """Create a standard HR user (no admin rights)."""

    email            = serializers.EmailField()
    password         = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)
    first_name       = serializers.CharField(max_length=50)
    last_name        = serializers.CharField(max_length=50)
    organization_id  = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        source="organization",
    )
    can_upload = serializers.BooleanField(default=False)

    def validate_email(self, value: str) -> str:
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value.lower()

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def validate(self, data: dict) -> dict:
        if data["password"] != data.pop("confirm_password"):
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return data

    def create(self, validated_data: dict):
        organization = validated_data.pop("organization", None)
        can_upload   = validated_data.pop("can_upload", False)
        password     = validated_data.pop("password")

        user, hr_user = User.objects.create_user(
            email=validated_data["email"],
            password=password,
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            organization=organization,
            can_upload=can_upload,
        )
        return hr_user


class RegisterAdminSerializer(RegisterUserSerializer):
    """Create an HR admin (is_admin=True, is_staff=True)."""

    organization_id = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        source="organization",
    )
    can_upload = serializers.BooleanField(default=True)

    def create(self, validated_data: dict):
        organization = validated_data.pop("organization", None)
        can_upload   = validated_data.pop("can_upload", True)
        password     = validated_data.pop("password")

        user, hr_user = User.objects.create_hr_admin(
            email=validated_data["email"],
            password=password,
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            organization=organization,
            can_upload=can_upload,
        )
        return hr_user


class RegisterSuperAdminSerializer(RegisterUserSerializer):
    """
    Create an HR Super Admin.
    Organization is required — super admins are always org-scoped.
    """

    organization_id = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.filter(is_active=True),
        required=True,
        source="organization",
    )

    def create(self, validated_data: dict):
        organization = validated_data.pop("organization")
        validated_data.pop("can_upload", None)
        password = validated_data.pop("password")

        user, hr_user = User.objects.create_super_admin(
            email=validated_data["email"],
            password=password,
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            organization=organization,
        )
        return hr_user


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class LoginSerializer(serializers.Serializer):
    email    = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data: dict) -> dict:
        user = authenticate(
            request=self.context.get("request"),
            username=data["email"].lower(),
            password=data["password"],
        )
        if not user:
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_active:
            raise serializers.ValidationError("This account has been deactivated.")
        if not hasattr(user, "hr_user") or not user.hr_user.is_active:
            raise serializers.ValidationError("No active HR profile found for this account.")

        data["user"] = user
        return data


class TokenResponseSerializer(serializers.Serializer):
    """Shape of the token payload returned after a successful login."""

    access  = serializers.CharField()
    refresh = serializers.CharField()
    user    = UserSerializer()
    profile = HRUserSerializer()


# ---------------------------------------------------------------------------
# Password management
# ---------------------------------------------------------------------------

class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password     = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value: str) -> str:
        validate_password(value)
        return value

    def validate(self, data: dict) -> dict:
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return data

    def validate_current_password(self, value: str) -> str:
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user


class RequestPasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        # Always return success to avoid user enumeration
        return value.lower()


class ConfirmPasswordResetSerializer(serializers.Serializer):
    token            = serializers.CharField()
    new_password     = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value: str) -> str:
        validate_password(value)
        return value

    def validate(self, data: dict) -> dict:
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return data


# ---------------------------------------------------------------------------
# Profile update
# ---------------------------------------------------------------------------

class UpdateProfileSerializer(serializers.ModelSerializer):
    """Allow a user to update their own first/last name."""

    class Meta:
        model  = User
        fields = ["first_name", "last_name"]

    def update(self, instance, validated_data):
        instance.first_name = validated_data.get("first_name", instance.first_name)
        instance.last_name  = validated_data.get("last_name", instance.last_name)
        instance.save(update_fields=["first_name", "last_name"])
        return instance


# ---------------------------------------------------------------------------
# Role & organization management (admin-only operations)
# ---------------------------------------------------------------------------

class AssignRolesSerializer(serializers.Serializer):
    is_admin       = serializers.BooleanField(required=False)
    is_super_admin = serializers.BooleanField(required=False)
    can_upload     = serializers.BooleanField(required=False)

    def validate(self, data: dict) -> dict:
        if not data:
            raise serializers.ValidationError(
                "At least one of `is_admin`, `is_super_admin`, or `can_upload` is required."
            )
        return data


class AssignOrganizationSerializer(serializers.Serializer):
    organization_id = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.filter(is_active=True),
        source="organization",
    )


class HRUserListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views."""

    email      = serializers.EmailField(source="user.email", read_only=True)
    full_name  = serializers.CharField(source="user.full_name", read_only=True)
    role_label = serializers.CharField(read_only=True)
    org_name   = serializers.CharField(source="organization.name", read_only=True, default=None)

    class Meta:
        model  = HRUser
        fields = [
            "id", "email", "full_name", "role_label",
            "org_name", "is_active", "can_upload", "created_at",
        ]
        read_only_fields = fields