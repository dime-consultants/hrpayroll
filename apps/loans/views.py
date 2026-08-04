"""
apps/loans/views.py

Endpoints:
  POST   /api/loans/uploads/                  → upload Excel (status: approval_pending)
  GET    /api/loans/uploads/                  → list uploads for the user's org
  GET    /api/loans/uploads/{id}/             → detail
  GET    /api/loans/uploads/{id}/status/      → lightweight poll endpoint (UI polling)
  GET    /api/loans/uploads/{id}/requests/    → all LoanRequest rows for this upload
  GET    /api/loans/batches/                  → list batches for the user's org
  GET    /api/loans/batches/{id}/             → batch detail
"""
import logging

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import LoanRequest, LoanRequestBatch, LoanRequestUpload
from .serializers import (
    LoanRequestBatchSerializer,
    LoanRequestSerializer,
    LoanRequestUploadCreateSerializer,
    LoanRequestUploadSerializer,
    LoanUploadStatusSerializer,
)

log = logging.getLogger(__name__)


def _get_hr_org(request):
    """Return the CheckoffOrganizationMirror for the requesting HR user, or raise."""
    try:
        return request.user.hr_profile.organization
    except Exception:
        raise PermissionDenied('User has no HR profile linked to an organisation.')


# ─────────────────────────────────────────────────────────────
# Upload list + create
# ─────────────────────────────────────────────────────────────

class LoanRequestUploadListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return LoanRequestUploadCreateSerializer
        return LoanRequestUploadSerializer

    def get_queryset(self):
        org = _get_hr_org(self.request)
        return (
            LoanRequestUpload.objects
            .filter(organization=org)
            .select_related('organization', 'uploaded_by', 'approved_by')
            .order_by('-date_created')
        )

    def create(self, request, *args, **kwargs):
        # Enforce upload permission
        try:
            hr = request.user.hr_profile
        except Exception:
            return Response({'detail': 'No HR profile found.'}, status=status.HTTP_403_FORBIDDEN)

        if not hr.can_upload:
            return Response(
                {'detail': 'You do not have permission to upload files.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        upload = serializer.save()

        log.info(
            'LoanRequestUpload created: id=%s org=%s user=%s file=%s',
            upload.id, upload.organization.code, request.user, upload.original_filename,
        )

        return Response(
            LoanRequestUploadSerializer(upload).data,
            status=status.HTTP_201_CREATED,
        )


# ─────────────────────────────────────────────────────────────
# Upload detail
# ─────────────────────────────────────────────────────────────

class LoanRequestUploadDetailView(generics.RetrieveAPIView):
    permission_classes  = [permissions.IsAuthenticated]
    serializer_class    = LoanRequestUploadSerializer

    def get_queryset(self):
        org = _get_hr_org(self.request)
        return LoanRequestUpload.objects.filter(organization=org)


# ─────────────────────────────────────────────────────────────
# Poll endpoint  — GET /api/loans/uploads/{id}/status/
# ─────────────────────────────────────────────────────────────

@method_decorator(never_cache, name='dispatch')
class LoanUploadStatusView(generics.RetrieveAPIView):
    """
    Lightweight polling endpoint.
    UI polls every N seconds while status is processing/approved.
    Returns status, counters, progress_percent, and batch status so the
    frontend knows when eligibility checks are done and when to show the
    "Batch ready for admin approval" state.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = LoanUploadStatusSerializer

    def get_queryset(self):
        org = _get_hr_org(self.request)
        return LoanRequestUpload.objects.filter(organization=org)


# ─────────────────────────────────────────────────────────────
# Loan requests for an upload
# ─────────────────────────────────────────────────────────────

class LoanRequestListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = LoanRequestSerializer
    filterset_fields   = ['status']

    def get_queryset(self):
        org       = _get_hr_org(self.request)
        upload_id = self.kwargs['upload_id']
        return (
            LoanRequest.objects
            .filter(upload_id=upload_id, organization=org)
            .order_by('row_number')
        )


# ─────────────────────────────────────────────────────────────
# Batch list + detail
# ─────────────────────────────────────────────────────────────

class LoanRequestBatchListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = LoanRequestBatchSerializer

    def get_queryset(self):
        org = _get_hr_org(self.request)
        return (
            LoanRequestBatch.objects
            .filter(organization=org)
            .select_related('organization', 'approved_by')
            .order_by('-date_created')
        )


class LoanRequestBatchDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = LoanRequestBatchSerializer

    def get_queryset(self):
        org = _get_hr_org(self.request)
        return LoanRequestBatch.objects.filter(organization=org)