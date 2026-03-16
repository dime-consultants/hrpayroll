import logging

log = logging.getLogger(__name__)


class OrganizationIsolationMiddleware:
    """
    Attaches request.hr_user and request.hr_organization on every request.
    All API views filter querysets by request.hr_organization — HR users
    can never see data from other organizations.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.hr_user = None
        request.hr_organization = None

        if hasattr(request, 'user') and request.user.is_authenticated:
            try:
                hr_profile = request.user.hr_profile
                if hr_profile.is_active:
                    request.hr_user = hr_profile
                    request.hr_organization = hr_profile.organization
            except Exception:
                pass  # superusers / staff have no hr_profile — that's fine

        return self.get_response(request)
