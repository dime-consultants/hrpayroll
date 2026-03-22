from rest_framework.permissions import BasePermission


class IsHRUser(BasePermission):
    message = 'You must be an active HR user to access this resource.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.hr_user)


class IsHRAdmin(BasePermission):
    message = 'Only HR Admins can perform this action.'

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
            and request.hr_user and request.hr_user.is_admin
        )


class CanUpload(BasePermission):
    message = 'You do not have permission to upload payroll data.'

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
            and request.hr_user and request.hr_user.can_upload
        )


class BelongsToOrganization(BasePermission):
    def has_object_permission(self, request, view, obj):
        if not request.hr_organization:
            return False
        org = getattr(obj, 'organization', None)
        return org is not None and org.id == request.hr_organization.id
