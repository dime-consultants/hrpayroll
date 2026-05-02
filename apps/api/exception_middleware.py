"""
Middleware to handle and gracefully respond to invalid host headers.

This prevents DisallowedHost exceptions from cluttering logs when
scanner/probes send requests with invalid Host headers (e.g., IPs
instead of domain names).
"""

from django.http import HttpResponse
from django.core.exceptions import DisallowedHost
import logging

logger = logging.getLogger('django')


class DisallowedHostMiddleware:
    """
    Catches DisallowedHost exceptions and returns a 400 response
    without logging to the error logs (only to debug logs).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            response = self.get_response(request)
        except DisallowedHost as e:
            # Log at DEBUG level only (not ERROR/WARNING) to reduce noise
            # from scanner probes and automated attacks
            logger.debug(
                f"DisallowedHost: {e}",
                extra={
                    'host': request.META.get('HTTP_HOST'),
                    'remote_addr': request.META.get('REMOTE_ADDR'),
                    'user_agent': request.META.get('HTTP_USER_AGENT'),
                }
            )
            # Return a generic 400 response
            return HttpResponse('Bad Request', status=400)
        
        return response
