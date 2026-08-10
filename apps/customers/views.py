"""
apps/customers/views.py

Endpoints:
  POST   /api/customers/registrations/                → submit borrower + KYC docs (status: approval_pending)
  GET    /api/customers/registrations/                 → list registrations for the user's org
  GET    /api/customers/registrations/{id}/            → detail, including nested kyc_documents
  GET    /api/customers/registrations/{id}/status/     → lightweight poll endpoint (UI polling)
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
