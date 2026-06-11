"""
HR User Management — Views
============================
All views use DRF APIView. JWT tokens are issued via
`rest_framework_simplejwt` — install it and configure in settings:

    INSTALLED_APPS = [..., "rest_framework_simplejwt"]

    SIMPLE_JWT = {
        "ACCESS_TOKEN_LIFETIME":  timedelta(hours=1),
        "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
        "ROTATE_REFRESH_TOKENS":  True,
        "BLACKLIST_AFTER_ROTATION": True,
    }

Signin / Signup quick-reference
---------------------------------
  POST  /auth/signin/                — sign in with email + password → tokens
  POST  /auth/signup/                — self-service: register as a standard HR user
  POST  /auth/signup/admin/          — register as HR admin (super admin only)
  POST  /auth/signup/super-admin/    — register as HR super admin (Django superuser only)
  POST  /auth/signout/               — blacklist refresh token
  POST  /auth/token/refresh/         — get a new access token
  POST  /auth/password/change/       — change own password (authenticated)
  POST  /auth/password/reset/        — request password-reset email
  POST  /auth/password/reset/confirm/ — confirm reset with token

User management (self)
  GET   /users/me/
  PATCH /users/me/

HR User management (admin)
  GET   /users/
  GET   /users/<pk>/
  POST  /users/<pk>/roles/
  POST  /users/<pk>/organization/
  POST  /users/<pk>/deactivate/
  POST  /users/<pk>/reactivate/

Organizations
  GET   /organizations/
  POST  /organizations/
  GET   /organizations/<pk>/
  PATCH /organizations/<pk>/
  GET   /organizations/<pk>/members/
  GET   /organizations/<pk>/admins/
"""

import logging

from django.contrib.auth import authenticate
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.conf import settings
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from .models import HRUser, Organization, User
from .permissions import IsHRAdmin, IsHRUser, IsHRSuperAdmin
from .serializers import (
    AssignOrganizationSerializer,
    AssignRolesSerializer,
    ChangePasswordSerializer,
    ConfirmPasswordResetSerializer,
    HRUserListSerializer,
    HRUserSerializer,
    LoginSerializer,
    OrganizationSerializer,
    OrganizationWriteSerializer,
    RegisterAdminSerializer,
    RegisterSuperAdminSerializer,
    RegisterUserSerializer,
    RequestPasswordResetSerializer,
    UpdateProfileSerializer,
    UserSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_token_response(user: User) -> dict:
    """
    Issue a fresh JWT pair for `user` and return the full signin payload.

    Shape:
        {
            "access":  "<access_token>",
            "refresh": "<refresh_token>",
            "user":    { id, email, first_name, last_name, full_name, ... },
            "profile": { id, role_label, is_admin, can_upload, organization, ... }
        }
    """
    refresh = RefreshToken.for_user(user)
    return {
        "access":  str(refresh.access_token),
        "refresh": str(refresh),
        "user":    UserSerializer(user).data,
        "profile": HRUserSerializer(user.hr_user).data,
    }


def _signin_user(request, email: str, password: str) -> Response:
    """
    Shared signin logic used by SignInView.
    Authenticates the user, validates their HR profile, and returns tokens.
    """
    # 1. Authenticate against the database
    user = authenticate(request=request, username=email.lower(), password=password)

    if user is None:
        logger.warning("Failed signin attempt for email=%s", email)
        return Response(
            {"detail": "Invalid email or password."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # 2. Account-level checks
    if not user.is_active:
        return Response(
            {"detail": "Your account has been deactivated. Please contact support."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # 3. HR profile checks
    try:
        hr_user = user.hr_user
    except HRUser.DoesNotExist:
        logger.error("User pk=%s has no HRUser profile", user.pk)
        return Response(
            {"detail": "No HR profile is associated with this account."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if not hr_user.is_active:
        return Response(
            {"detail": "Your HR profile is inactive. Please contact your administrator."},
            status=status.HTTP_403_FORBIDDEN,
        )

    logger.info("Signin success — User pk=%s role=%s", user.pk, hr_user.role_label)
    return Response(_build_token_response(user), status=status.HTTP_200_OK)


# ===========================================================================
# Sign In
# ===========================================================================

class SignInView(APIView):
    """
    POST /auth/signin/

    Sign in with email and password. Returns a JWT access + refresh token
    pair along with the user's profile.

    Anyone can call this endpoint — no authentication required.

    Request body:
        {
            "email":    "jane@acme.com",
            "password": "s3cur3p@ss"
        }

    Response 200:
        {
            "access":  "<jwt_access_token>",
            "refresh": "<jwt_refresh_token>",
            "user": {
                "id": 1,
                "email": "jane@acme.com",
                "first_name": "Jane",
                "last_name": "Smith",
                "full_name": "Jane Smith",
                "is_active": true,
                "is_staff": false
            },
            "profile": {
                "id": 1,
                "role_label": "Admin",
                "is_admin": true,
                "is_super_admin": false,
                "can_upload": true,
                "organization": { "id": 3, "name": "Acme Corp", ... },
                ...
            }
        }

    Error responses:
        401 — wrong email or password
        403 — account or HR profile is inactive
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(
                {"detail": "Invalid request.", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email    = serializer.validated_data["email"]
        password = request.data.get("password", "")
        return _signin_user(request, email, password)


# ===========================================================================
# Sign Up (self-service registration)
# ===========================================================================

class SignUpView(APIView):
    """
    POST /auth/signup/

    Self-service registration for a standard HR user.
    No authentication required — anyone with a valid payload can sign up.

    On success the user is signed in immediately and tokens are returned,
    so the client doesn't need a separate signin call after registration.

    Request body:
        {
            "email":            "jane@acme.com",
            "password":         "s3cur3p@ss",
            "confirm_password": "s3cur3p@ss",
            "first_name":       "Jane",
            "last_name":        "Smith",
            "organization_id":  3,        // optional
            "can_upload":       false      // optional, default false
        }

    Response 201:
        {
            "access":  "<jwt_access_token>",
            "refresh": "<jwt_refresh_token>",
            "user":    { ... },
            "profile": { ... }
        }

    Error responses:
        400 — validation failure (duplicate email, password mismatch, etc.)
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterUserSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"detail": "Registration failed.", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        hr_user = serializer.save()
        user    = hr_user.user

        logger.info(
            "Signup success — User pk=%s email=%s org=%s",
            user.pk, user.email,
            hr_user.organization.name if hr_user.organization else "none",
        )

        # Sign in immediately — return tokens alongside the profile
        return Response(_build_token_response(user), status=status.HTTP_201_CREATED)


class SignUpAdminView(APIView):
    """
    POST /auth/signup/admin/

    Register a new HR Admin account.
    Requires the caller to already be authenticated as an HR Super Admin.

    The new admin is NOT automatically signed in — an admin account is
    provisioned for someone else, so no tokens are returned.

    Request body:
        {
            "email":            "admin@acme.com",
            "password":         "s3cur3p@ss",
            "confirm_password": "s3cur3p@ss",
            "first_name":       "John",
            "last_name":        "Doe",
            "organization_id":  3,       // optional
            "can_upload":       true     // optional, default true for admins
        }

    Response 201:
        {
            "detail": "Admin account created successfully.",
            "profile": { ... }
        }

    Error responses:
        400 — validation failure
        403 — caller is not an HR Super Admin
    """

    permission_classes = [IsAuthenticated, IsHRSuperAdmin]

    def post(self, request):
        serializer = RegisterAdminSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"detail": "Registration failed.", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        hr_user = serializer.save()
        logger.info(
            "Admin signup — new HRUser pk=%s created by User pk=%s",
            hr_user.pk, request.user.pk,
        )
        return Response(
            {
                "detail": "Admin account created successfully.",
                "profile": HRUserSerializer(hr_user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class SignUpSuperAdminView(APIView):
    """
    POST /auth/signup/super-admin/

    Register a new HR Super Admin account.
    Restricted to Django superusers only.

    `organization_id` is required — super admins are always org-scoped.

    Request body:
        {
            "email":            "lead@acme.com",
            "password":         "s3cur3p@ss",
            "confirm_password": "s3cur3p@ss",
            "first_name":       "Alice",
            "last_name":        "Wanjiru",
            "organization_id":  3         // required
        }

    Response 201:
        {
            "detail": "Super admin account created successfully.",
            "profile": { ... }
        }

    Error responses:
        400 — validation failure
        403 — caller is not a Django superuser
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_superuser:
            return Response(
                {"detail": "Only Django superusers can create super admin accounts."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = RegisterSuperAdminSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"detail": "Registration failed.", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        hr_user = serializer.save()
        logger.info(
            "Super admin signup — new HRUser pk=%s created by User pk=%s",
            hr_user.pk, request.user.pk,
        )
        return Response(
            {
                "detail": "Super admin account created successfully.",
                "profile": HRUserSerializer(hr_user).data,
            },
            status=status.HTTP_201_CREATED,
        )


# ===========================================================================
# Sign Out
# ===========================================================================

class SignOutView(APIView):
    """
    POST /auth/signout/

    Blacklist the user's refresh token, invalidating their session.
    The access token will expire naturally (honour its TTL).

    Request body:
        { "refresh": "<refresh_token>" }

    Response 200:
        { "detail": "Successfully signed out." }

    Error responses:
        400 — missing or already-blacklisted token
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"detail": "`refresh` token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError as exc:
            return Response(
                {"detail": f"Token error: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info("Signout — User pk=%s", request.user.pk)
        return Response({"detail": "Successfully signed out."}, status=status.HTTP_200_OK)





class ChangePasswordView(APIView):
    """
    POST /auth/password/change/

    Change password for the currently authenticated user.

    Request:
        { "current_password": "...", "new_password": "...", "confirm_password": "..." }
    """

    permission_classes = [IsAuthenticated, IsHRUser]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info("Password changed for User pk=%s", request.user.pk)
        return Response({"detail": "Password updated successfully."}, status=status.HTTP_200_OK)


class RequestPasswordResetView(APIView):
    """
    POST /auth/password/reset/

    Send a password-reset email. Always returns 200 to prevent enumeration.

    Request:
        { "email": "user@example.com" }
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RequestPasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        try:
            user  = User.objects.get(email=email, is_active=True)
            token = default_token_generator.make_token(user)
            reset_url = f"{settings.FRONTEND_URL}/auth/password/reset/confirm/?uid={user.pk}&token={token}"
            send_mail(
                subject="Password Reset Request",
                message=f"Use this link to reset your password: {reset_url}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=True,
            )
            logger.info("Password reset email sent to User pk=%s", user.pk)
        except User.DoesNotExist:
            # Intentional no-op — same response regardless
            pass

        return Response(
            {"detail": "If an account with that email exists, a reset link has been sent."},
            status=status.HTTP_200_OK,
        )


class ConfirmPasswordResetView(APIView):
    """
    POST /auth/password/reset/confirm/

    Confirm a password reset using the token from the email link.

    Request:
        { "token": "uid:token", "new_password": "...", "confirm_password": "..." }
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ConfirmPasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            uid, token = serializer.validated_data["token"].split(":", 1)
            user = User.objects.get(pk=uid, is_active=True)
        except (ValueError, User.DoesNotExist):
            return Response(
                {"detail": "Invalid or expired reset token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "Invalid or expired reset token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        logger.info("Password reset confirmed for User pk=%s", user.pk)
        return Response({"detail": "Password has been reset successfully."}, status=status.HTTP_200_OK)




# ===========================================================================
# Current user (self)
# ===========================================================================

class MeView(APIView):
    """
    GET  /users/me/   — Return the authenticated user's profile.
    PATCH /users/me/  — Update first_name / last_name.
    """

    permission_classes = [IsAuthenticated, IsHRUser]

    def get(self, request):
        return Response(HRUserSerializer(request.hr_user).data)

    def patch(self, request):
        serializer = UpdateProfileSerializer(
            instance=request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # Re-fetch hr_user to pick up any reverse relation changes
        request.hr_user.refresh_from_db()
        return Response(HRUserSerializer(request.hr_user).data)


# ===========================================================================
# HR User management (admin operations)
# ===========================================================================

class HRUserListView(APIView):
    """
    GET /users/

    Return all active HR users. Scoped to the admin's organization
    unless the caller is a superuser (sees all).

    Query params:
        organization  — filter by organization pk
        role          — filter by role: user | admin | super_admin
        search        — partial match on email / name
    """

    permission_classes = [IsAuthenticated, IsHRAdmin]

    def get(self, request):
        qs = HRUser.objects.select_related("user", "organization").order_by("user__email")

        # Superusers see everyone; admins are scoped to their org
        if not request.user.is_superuser and request.hr_user:
            qs = qs.filter(organization=request.hr_user.organization)

        if org_pk := request.query_params.get("organization"):
            qs = qs.filter(organization__pk=org_pk)

        if role := request.query_params.get("role"):
            if role == "super_admin":
                qs = qs.filter(is_super_admin=True)
            elif role == "admin":
                qs = qs.filter(is_admin=True, is_super_admin=False)
            elif role == "user":
                qs = qs.filter(is_admin=False, is_super_admin=False)

        if search := request.query_params.get("search"):
            qs = qs.filter(user__email__icontains=search) | qs.filter(
                user__first_name__icontains=search
            ) | qs.filter(user__last_name__icontains=search)

        return Response(HRUserListSerializer(qs, many=True).data)


class HRUserDetailView(APIView):
    """
    GET /users/<pk>/

    Retrieve a single HR user's full profile.
    """

    permission_classes = [IsAuthenticated, IsHRAdmin]

    def get(self, request, pk: int):
        hr_user = get_object_or_404(
            HRUser.objects.select_related("user", "organization"), pk=pk
        )
        return Response(HRUserSerializer(hr_user).data)


class AssignRolesView(APIView):
    """
    POST /users/<pk>/roles/

    Assign or modify roles on an HR user.
    Super admin status can only be granted by a Django superuser.

    Request:
        {
            "is_admin":       true,   // optional
            "is_super_admin": false,  // optional — superuser only
            "can_upload":     true    // optional
        }
    """

    permission_classes = [IsAuthenticated, IsHRAdmin]

    def post(self, request, pk: int):
        hr_user = get_object_or_404(HRUser, pk=pk)
        serializer = AssignRolesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # Only Django superusers can touch is_super_admin
        if "is_super_admin" in data and not request.user.is_superuser:
            return Response(
                {"detail": "Only Django superusers can assign super admin status."},
                status=status.HTTP_403_FORBIDDEN,
            )

        updated = User.objects.assign_roles(hr_user, **data)
        logger.info(
            "Roles updated on HRUser pk=%s by User pk=%s — %s",
            updated.pk, request.user.pk, data,
        )
        return Response(HRUserSerializer(updated).data)


class AssignOrganizationView(APIView):
    """
    POST /users/<pk>/organization/

    Assign or transfer an HR user to an organization.
    Super admins only.

    Request:
        { "organization_id": 3 }
    """

    permission_classes = [IsAuthenticated, IsHRSuperAdmin]

    def post(self, request, pk: int):
        hr_user = get_object_or_404(HRUser, pk=pk)
        serializer = AssignOrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        org     = serializer.validated_data["organization"]
        updated = User.objects.assign_organization(hr_user, org)
        logger.info(
            "HRUser pk=%s assigned to Organization pk=%s by User pk=%s",
            updated.pk, org.pk, request.user.pk,
        )
        return Response(HRUserSerializer(updated).data)


class DeactivateUserView(APIView):
    """
    POST /users/<pk>/deactivate/

    Soft-deactivate an HR user and their underlying account.
    HR Admins only.
    """

    permission_classes = [IsAuthenticated, IsHRAdmin]

    def post(self, request, pk: int):
        hr_user = get_object_or_404(HRUser, pk=pk)

        if hr_user.user.pk == request.user.pk:
            return Response(
                {"detail": "You cannot deactivate your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = User.objects.deactivate(hr_user)
        logger.info("HRUser pk=%s deactivated by User pk=%s", updated.pk, request.user.pk)
        return Response(HRUserSerializer(updated).data)


class ReactivateUserView(APIView):
    """
    POST /users/<pk>/reactivate/

    Re-activate a previously deactivated HR user.
    HR Admins only.
    """

    permission_classes = [IsAuthenticated, IsHRAdmin]

    def post(self, request, pk: int):
        hr_user = get_object_or_404(HRUser, pk=pk)
        updated = User.objects.reactivate(hr_user)
        logger.info("HRUser pk=%s reactivated by User pk=%s", updated.pk, request.user.pk)
        return Response(HRUserSerializer(updated).data)


# ===========================================================================
# Organizations
# ===========================================================================

class OrganizationListView(APIView):
    """
    GET  /organizations/  — List all active organizations.
    POST /organizations/  — Create a new organization (super admins only).
    """

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsHRSuperAdmin()]
        return [IsAuthenticated(), IsHRUser()]

    def get(self, request):
        qs = Organization.objects.filter(is_active=True)
        # Non-superusers only see their own org
        if not request.user.is_superuser and request.hr_user and request.hr_user.organization:
            qs = qs.filter(pk=request.hr_user.organization.pk)
        return Response(OrganizationSerializer(qs, many=True).data)

    def post(self, request):
        serializer = OrganizationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org = serializer.save()
        logger.info("Organization pk=%s created by User pk=%s", org.pk, request.user.pk)
        return Response(OrganizationSerializer(org).data, status=status.HTTP_201_CREATED)


class OrganizationDetailView(APIView):
    """
    GET   /organizations/<pk>/  — Retrieve org details.
    PATCH /organizations/<pk>/  — Update org (super admins only).
    """

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsAuthenticated(), IsHRSuperAdmin()]
        return [IsAuthenticated(), IsHRUser()]

    def _get_org(self, pk: int) -> Organization:
        return get_object_or_404(Organization, pk=pk)

    def get(self, request, pk: int):
        org = self._get_org(pk)
        return Response(OrganizationSerializer(org).data)

    def patch(self, request, pk: int):
        org = self._get_org(pk)
        serializer = OrganizationWriteSerializer(org, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        org = serializer.save()
        logger.info("Organization pk=%s updated by User pk=%s", org.pk, request.user.pk)
        return Response(OrganizationSerializer(org).data)


class OrganizationMembersView(APIView):
    """
    GET /organizations/<pk>/members/

    List all active HR users in an organization.
    """

    permission_classes = [IsAuthenticated, IsHRAdmin]

    def get(self, request, pk: int):
        org = get_object_or_404(Organization, pk=pk)
        members = User.objects.get_organization_members(org)
        return Response(HRUserListSerializer(members, many=True).data)


class OrganizationAdminsView(APIView):
    """
    GET /organizations/<pk>/admins/

    List all active HR admins in an organization.
    """

    permission_classes = [IsAuthenticated, IsHRAdmin]

    def get(self, request, pk: int):
        org = get_object_or_404(Organization, pk=pk)
        admins = User.objects.get_organization_admins(org)
        return Response(HRUserListSerializer(admins, many=True).data)