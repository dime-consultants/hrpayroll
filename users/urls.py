"""
HR User Management — URL Configuration
=======================================
Include in your project's root urls.py:

    from django.urls import path, include

    urlpatterns = [
        path("api/v1/", include("your_app.urls")),
    ]

Full endpoint map
-----------------
Auth
    POST   api/v1/auth/signin/
    POST   api/v1/auth/signup/
    POST   api/v1/auth/signup/admin/
    POST   api/v1/auth/signup/super-admin/
    POST   api/v1/auth/signout/
    POST   api/v1/auth/token/refresh/
    POST   api/v1/auth/password/change/
    POST   api/v1/auth/password/reset/
    POST   api/v1/auth/password/reset/confirm/

Current user (self)
    GET    api/v1/users/me/
    PATCH  api/v1/users/me/

HR User management (admin)
    GET    api/v1/users/
    GET    api/v1/users/<pk>/
    POST   api/v1/users/<pk>/roles/
    POST   api/v1/users/<pk>/organization/
    POST   api/v1/users/<pk>/deactivate/
    POST   api/v1/users/<pk>/reactivate/

Organizations
    GET    api/v1/organizations/
    POST   api/v1/organizations/
    GET    api/v1/organizations/<pk>/
    PATCH  api/v1/organizations/<pk>/
    GET    api/v1/organizations/<pk>/members/
    GET    api/v1/organizations/<pk>/admins/
"""

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

app_name = "hr_users"

urlpatterns = [

    # -----------------------------------------------------------------------
    # Auth — Signin / Signup / Signout
    # -----------------------------------------------------------------------
    path(
        "auth/signin/",
        views.SignInView.as_view(),
        name="auth-signin",
    ),
    path(
        "auth/signup/",
        views.SignUpView.as_view(),
        name="auth-signup",
    ),
    path(
        "auth/signup/admin/",
        views.SignUpAdminView.as_view(),
        name="auth-signup-admin",
    ),
    path(
        "auth/signup/super-admin/",
        views.SignUpSuperAdminView.as_view(),
        name="auth-signup-super-admin",
    ),
    path(
        "auth/signout/",
        views.SignOutView.as_view(),
        name="auth-signout",
    ),

    # -----------------------------------------------------------------------
    # Auth — Token & Password
    # -----------------------------------------------------------------------
    path(
        "auth/token/refresh/",
        TokenRefreshView.as_view(),
        name="auth-token-refresh",
    ),
    path(
        "auth/password/change/",
        views.ChangePasswordView.as_view(),
        name="auth-password-change",
    ),
    path(
        "auth/password/reset/",
        views.RequestPasswordResetView.as_view(),
        name="auth-password-reset",
    ),
    path(
        "auth/password/reset/confirm/",
        views.ConfirmPasswordResetView.as_view(),
        name="auth-password-reset-confirm",
    ),

    # -----------------------------------------------------------------------
    # Current user (self)
    # -----------------------------------------------------------------------
    path(
        "users/me/",
        views.MeView.as_view(),
        name="user-me",
    ),

    # -----------------------------------------------------------------------
    # HR User management (admin)
    # -----------------------------------------------------------------------
    path(
        "users/",
        views.HRUserListView.as_view(),
        name="user-list",
    ),
    path(
        "users/<int:pk>/",
        views.HRUserDetailView.as_view(),
        name="user-detail",
    ),
    path(
        "users/<int:pk>/roles/",
        views.AssignRolesView.as_view(),
        name="user-assign-roles",
    ),
    path(
        "users/<int:pk>/organization/",
        views.AssignOrganizationView.as_view(),
        name="user-assign-organization",
    ),
    path(
        "users/<int:pk>/deactivate/",
        views.DeactivateUserView.as_view(),
        name="user-deactivate",
    ),
    path(
        "users/<int:pk>/reactivate/",
        views.ReactivateUserView.as_view(),
        name="user-reactivate",
    ),

    # -----------------------------------------------------------------------
    # Organizations
    # -----------------------------------------------------------------------
    path(
        "organizations/",
        views.OrganizationListView.as_view(),
        name="organization-list",
    ),
    path(
        "organizations/<int:pk>/",
        views.OrganizationDetailView.as_view(),
        name="organization-detail",
    ),
    path(
        "organizations/<int:pk>/members/",
        views.OrganizationMembersView.as_view(),
        name="organization-members",
    ),
    path(
        "organizations/<int:pk>/admins/",
        views.OrganizationAdminsView.as_view(),
        name="organization-admins",
    ),
]