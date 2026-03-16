import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

log = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is None:
        log.exception('Unhandled exception in %s: %s', context.get('view'), exc)
        return Response(
            {'detail': 'An unexpected error occurred.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    if isinstance(response.data, dict) and 'detail' not in response.data:
        response.data = {'detail': response.data}

    return response
