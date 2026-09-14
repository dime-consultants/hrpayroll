"""
apps/customers/views.py

Endpoints:
  POST   /api/customers/registrations/                → submit borrower + KYC docs (status: approval_pending)
  GET    /api/customers/registrations/                 → list registrations for the user's org
  GET    /api/customers/registrations/{id}/            → detail, including nested kyc_documents
  GET    /api/customers/registrations/{id}/status/     → lightweight poll endpoint (UI polling)
  POST   /api/customers/registrations/{id}/sync-status/ → re-fetch this registration's status from the LMS
"""
import logging

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from apps.organizations.utils import get_hr_org
from .models import CustomerRegistration
from .serializers import (
    CustomerRegistrationCreateSerializer,
    CustomerRegistrationSerializer,
    CustomerRegistrationStatusSerializer,
)

log = logging.getLogger(__name__)


class CustomerRegistrationListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return CustomerRegistrationCreateSerializer
        return CustomerRegistrationSerializer

    def get_queryset(self):
        org = get_hr_org(self.request)
        return (
            CustomerRegistration.objects
            .filter(organization=org)
            .select_related('organization', 'submitted_by', 'approved_by')
            .prefetch_related('kyc_documents')
            .order_by('-date_created')
        )

    def create(self, request, *args, **kwargs):
        try:
            hr = request.user.hr_profile
        except Exception:
            return Response({'detail': 'No HR profile found.'}, status=status.HTTP_403_FORBIDDEN)

        if not hr.can_upload:
            return Response(
                {'detail': 'You do not have permission to register customers.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        registration = serializer.save()

        log.info(
            'CustomerRegistration created: id=%s org=%s user=%s phone=%s',
            registration.id, registration.organization.code, request.user, registration.phone_number,
        )

        return Response(
            CustomerRegistrationSerializer(registration).data,
            status=status.HTTP_201_CREATED,
        )


class CustomerRegistrationDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = CustomerRegistrationSerializer

    def get_queryset(self):
        org = get_hr_org(self.request)
        return (
            CustomerRegistration.objects
            .filter(organization=org)
            .prefetch_related('kyc_documents')
        )


@method_decorator(never_cache, name='dispatch')
class CustomerRegistrationStatusView(generics.RetrieveAPIView):
    """Lightweight polling endpoint — UI polls while status is processing."""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = CustomerRegistrationStatusSerializer

    def get_queryset(self):
        org = get_hr_org(self.request)
        return CustomerRegistration.objects.filter(organization=org).prefetch_related('kyc_documents')


class CustomerRegistrationSyncStatusView(generics.GenericAPIView):
    """
    POST /api/customers/registrations/{id}/sync-status/

    Re-fetches this registration's status from the LMS by phone number
    (same lookup admin's "reprocess registrations" action uses) and
    applies it, so HR can refresh a stuck/uncertain registration on demand
    without waiting for the next admin approval cycle.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = CustomerRegistrationSerializer

    def get_queryset(self):
        org = get_hr_org(self.request)
        return CustomerRegistration.objects.filter(organization=org).prefetch_related('kyc_documents')

    def post(self, request, *args, **kwargs):
        from apps.api.lms_client import get_customer_exclusive

        LMS_STATUS_MAP = {
            'active':   CustomerRegistration.STATUS_ACTIVE,
            'inactive': CustomerRegistration.STATUS_FAILED,
            'pending':  CustomerRegistration.STATUS_PROCESSING,
        }

        registration = self.get_object()

        if not registration.phone_number:
            return Response(
                {'detail': 'Registration has no phone number to sync against.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = get_customer_exclusive(registration.phone_number)
        lms_status_raw = result.get('status') if result else None
        mapped = LMS_STATUS_MAP.get(lms_status_raw.lower()) if lms_status_raw else None

        if mapped and mapped != registration.status:
            registration.status = mapped
            registration.save(update_fields=['status'])
            log.info(
                'CustomerRegistrationSyncStatusView: registration=%s synced status -> %s',
                registration.id, mapped,
            )

        return Response(CustomerRegistrationSerializer(registration).data)
