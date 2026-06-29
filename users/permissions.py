from rest_framework.permissions import BasePermission


class IsHRUser(BasePermission):
    message = 'You must be an active HR user to access this resource.'

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and hasattr(request.user, "hr_user")
            and request.user.hr_user.is_active
        )


class IsHRAdmin(BasePermission):
    message = 'Only HR Admins can perform this action.'

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and hasattr(request.user, "hr_user")
            and request.user.hr_user.is_active
            and request.user.hr_user.is_admin
        )


class IsHRSuperAdmin(BasePermission):
    message = 'Only HR Super Admins can perform this action.'

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and hasattr(request.user, "hr_user")
            and request.user.hr_user.is_active
            and request.user.hr_user.is_super_admin
        )
