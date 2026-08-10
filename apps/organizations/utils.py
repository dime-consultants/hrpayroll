"""
apps/organizations/utils.py

Shared helpers for resolving the requesting HR user's organisation.
Used by any app-scoped API view that needs to filter querysets to the
caller's own CheckoffOrganizationMirror (loans, customers, ...).
"""
from rest_framework.exceptions import PermissionDenied


def get_hr_org(request):
    """Return the CheckoffOrganizationMirror for the requesting HR user, or raise."""
    try:
        return request.user.hr_profile.organization
    except Exception:
        raise PermissionDenied('User has no HR profile linked to an organisation.')
