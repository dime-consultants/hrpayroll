from rest_framework.permissions import BasePermission


class IsHRUser(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and hasattr(request.user, "hr_user")
            and request.user.hr_user.is_active
        )


class IsHRAdmin(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and hasattr(request.user, "hr_user")
            and request.user.hr_user.is_active
            and request.user.hr_user.is_admin
        )


class IsHRSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and hasattr(request.user, "hr_user")
            and request.user.hr_user.is_active
            and request.user.hr_user.is_super_admin
        )
