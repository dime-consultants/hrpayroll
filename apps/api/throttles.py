from rest_framework.throttling import UserRateThrottle


class RepaymentRateThrottle(UserRateThrottle):
    """30 repayment dispatches per minute, scoped per organization."""
    scope = 'repayment'

    def get_cache_key(self, request, view):
        org_id = getattr(request.hr_organization, 'id', 'anon')
        return self.cache_format % {'scope': self.scope, 'ident': str(org_id)}


class BurstRepaymentThrottle(UserRateThrottle):
    """Hard burst cap: 5 per second."""
    scope = 'repayment_burst'
