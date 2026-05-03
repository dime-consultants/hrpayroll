# apps/api/decorators.py
from functools import wraps
from django.http import JsonResponse


def partner_authenticated(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        auth = request.headers.get('Authorization', '')
        token = auth[len('Bearer '):] if auth.startswith('Bearer ') else ''
        if not token:
            return JsonResponse({'code': '401', 'message': 'Unauthorized'}, status=401)
        try:
            from apps.api.lms_client import _get_auth_token 
            expected = _get_auth_token()
        except Exception:
            return JsonResponse({'code': '500', 'message': 'Auth unavailable'}, status=500)
        if token != expected:
            return JsonResponse({'code': '401', 'message': 'Invalid token'}, status=401)
        return view_func(request, *args, **kwargs)
    return wrapped