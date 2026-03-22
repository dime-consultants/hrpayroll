import logging
from decimal import Decimal

from django.db.models import Sum, Count, Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics, status, filters
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.organizations.models import CheckoffOrganizationMirror, HRUser, AuditLog
from apps.payroll.models import PayrollUpload, SalaryDeduction
from apps.repayments.models import RepaymentBatch, RepaymentRecord

from .permissions import IsHRUser, IsHRAdmin, CanUpload, BelongsToOrganization
from .serializers import (
    CheckoffOrganizationSerializer,
    HRUserSerializer, HRUserCreateSerializer,
    PayrollUploadSerializer, PayrollUploadCreateSerializer,
    SalaryDeductionSerializer,
    RepaymentBatchSerializer, RepaymentRecordSerializer,
    BatchApproveSerializer, DashboardSummarySerializer,
)
from .throttles import RepaymentRateThrottle, BurstRepaymentThrottle

log = logging.getLogger(__name__)


class HRTokenObtainView(TokenObtainPairView):
    pass


class HRTokenRefreshView(TokenRefreshView):
    pass


class OrgScopedMixin:
    """Mixin that scopes all querysets to request.hr_organization."""
    permission_classes = [IsHRUser]

    def get_org(self):
        if self.request.user.is_superuser:
            code = self.request.query_params.get('org_code')
            if code:
                return CheckoffOrganizationMirror.objects.filter(code=code).first()
        return self.request.hr_organization

    def _audit(self, action, obj_id='', description='', metadata=None):
        AuditLog.objects.create(
            actor=self.request.user,
            organization=self.request.hr_organization,
            action=action,
            object_id=str(obj_id),
            description=description,
            ip_address=self._get_ip(),
            metadata=metadata or {},
        )

    def _get_ip(self):
        xff = self.request.META.get('HTTP_X_FORWARDED_FOR')
        return xff.split(',')[0].strip() if xff else self.request.META.get('REMOTE_ADDR', '')


class DashboardView(OrgScopedMixin, APIView):
    def get(self, request):
        org = self.get_org()
        if not org:
            return Response({'detail': 'No organization found.'}, status=404)

        period = request.query_params.get('period', timezone.now().strftime('%Y-%m'))
        uploads = PayrollUpload.objects.filter(organization=org)
        deductions = SalaryDeduction.objects.filter(organization=org)

        try:
            year, month = map(int, period.split('-'))
            uploads = uploads.filter(payroll_period__year=year, payroll_period__month=month)
            deductions = deductions.filter(deduction_date__year=year, deduction_date__month=month)
        except Exception:
            pass

        agg = deductions.aggregate(
            total_amount=Sum('amount'),
            failed=Count('id', filter=Q(status=SalaryDeduction.STATUS_FAILED)),
        )
        succ_agg = RepaymentRecord.objects.filter(
            organization=org, status=RepaymentRecord.STATUS_SUCCESS,
        ).aggregate(total=Sum('amount_sent'))
        pending_batches = RepaymentBatch.objects.filter(
            organization=org, status=RepaymentBatch.STATUS_DRAFT
        ).count()

        data = {
            'organization': org.name,
            'period': period,
            'total_uploads': uploads.count(),
            'total_deductions': deductions.count(),
            'total_amount': agg['total_amount'] or Decimal('0'),
            'successful_amount': succ_agg['total'] or Decimal('0'),
            'pending_batches': pending_batches,
            'failed_deductions': agg['failed'] or 0,
        }
        return Response(DashboardSummarySerializer(data).data)


class OrganizationDetailView(OrgScopedMixin, generics.RetrieveAPIView):
    serializer_class = CheckoffOrganizationSerializer

    def get_object(self):
        org = self.get_org()
        if not org:
            from rest_framework.exceptions import NotFound
            raise NotFound('Organization not found.')
        return org


class HRUserListCreateView(OrgScopedMixin, generics.ListCreateAPIView):
    def get_permissions(self):
        return [IsHRAdmin()] if self.request.method == 'POST' else [IsHRUser()]

    def get_serializer_class(self):
        return HRUserCreateSerializer if self.request.method == 'POST' else HRUserSerializer

    def get_queryset(self):
        return HRUser.objects.filter(organization=self.get_org()).select_related('user', 'organization')

    def perform_create(self, serializer):
        hr_user = serializer.save()
        self._audit('user_create', hr_user.id, f'Created HR user {hr_user.user.username}')


class HRUserDetailView(OrgScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsHRAdmin]
    serializer_class = HRUserSerializer

    def get_queryset(self):
        return HRUser.objects.filter(organization=self.get_org())

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.user.is_active = False
        instance.save(update_fields=['is_active'])
        instance.user.save(update_fields=['is_active'])
        self._audit('user_deactivate', instance.id, f'Deactivated {instance.user.username}')


class PayrollUploadListCreateView(OrgScopedMixin, generics.ListCreateAPIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'payroll_period']
    ordering_fields = ['date_created', 'payroll_period']
    ordering = ['-date_created']

    def get_permissions(self):
        return [CanUpload()] if self.request.method == 'POST' else [IsHRUser()]

    def get_serializer_class(self):
        return PayrollUploadCreateSerializer if self.request.method == 'POST' else PayrollUploadSerializer

    def get_queryset(self):
        return PayrollUpload.objects.filter(organization=self.get_org()).select_related(
            'organization', 'uploaded_by'
        )

    def perform_create(self, serializer):
        upload = serializer.save()
        self._audit('upload', upload.id,
                    f'Uploaded {upload.original_filename} for {upload.payroll_period:%Y-%m}',
                    metadata={'filename': upload.original_filename})

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            PayrollUploadSerializer(serializer.instance, context={'request': request}).data,
            status=status.HTTP_202_ACCEPTED
        )


class PayrollUploadDetailView(OrgScopedMixin, generics.RetrieveAPIView):
    serializer_class = PayrollUploadSerializer
    permission_classes = [IsHRUser, BelongsToOrganization]

    def get_queryset(self):
        return PayrollUpload.objects.filter(organization=self.get_org())


class SalaryDeductionListView(OrgScopedMixin, generics.ListAPIView):
    serializer_class = SalaryDeductionSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'deduction_date', 'upload']
    search_fields = ['phone_number', 'employee_name', 'employee_id', 'reference']
    ordering_fields = ['deduction_date', 'amount', 'date_created']
    ordering = ['-date_created']

    def get_queryset(self):
        return SalaryDeduction.objects.filter(organization=self.get_org()).select_related(
            'organization', 'upload'
        )


class SalaryDeductionDetailView(OrgScopedMixin, generics.RetrieveAPIView):
    serializer_class = SalaryDeductionSerializer
    permission_classes = [IsHRUser, BelongsToOrganization]

    def get_queryset(self):
        return SalaryDeduction.objects.filter(organization=self.get_org())


class RepaymentBatchListView(OrgScopedMixin, generics.ListAPIView):
    serializer_class = RepaymentBatchSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status']
    ordering_fields = ['date_created', 'total_amount']
    ordering = ['-date_created']

    def get_queryset(self):
        return RepaymentBatch.objects.filter(organization=self.get_org()).select_related(
            'organization', 'approved_by', 'upload'
        )


class RepaymentBatchDetailView(OrgScopedMixin, generics.RetrieveAPIView):
    serializer_class = RepaymentBatchSerializer
    permission_classes = [IsHRUser, BelongsToOrganization]

    def get_queryset(self):
        return RepaymentBatch.objects.filter(organization=self.get_org())


class RepaymentBatchApproveView(OrgScopedMixin, APIView):
    """
    POST /api/v1/batches/{id}/approve/
    HR Admin approves a DRAFT batch — triggers Celery fan-out.
    """
    permission_classes = [IsHRAdmin]
    throttle_classes = [RepaymentRateThrottle, BurstRepaymentThrottle]

    def post(self, request, pk):
        from apps.repayments.tasks import dispatch_repayment_batch

        org = self.get_org()
        try:
            batch = RepaymentBatch.objects.get(id=pk, organization=org)
        except RepaymentBatch.DoesNotExist:
            return Response({'detail': 'Batch not found.'}, status=404)

        if batch.status != RepaymentBatch.STATUS_DRAFT:
            return Response(
                {'detail': f'Batch is "{batch.status}" — only DRAFT batches can be approved.'},
                status=400
            )

        serializer = BatchApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        batch.approve(request.user)
        task = dispatch_repayment_batch.delay(str(batch.id))
        batch.celery_task_id = task.id
        batch.status = RepaymentBatch.STATUS_DISPATCHING
        batch.save(update_fields=['celery_task_id', 'status'])

        self._audit(
            'batch_approve', batch.id,
            f'Approved batch {str(batch.id)[:8]} — {batch.total_deductions} deductions, KES {batch.total_amount}',
            metadata={'task_id': task.id}
        )

        return Response(RepaymentBatchSerializer(batch).data)


class RepaymentRecordListView(OrgScopedMixin, generics.ListAPIView):
    serializer_class = RepaymentRecordSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'batch']
    search_fields = ['phone_number', 'lms_response_code']
    ordering_fields = ['dispatched_at', 'amount_sent']
    ordering = ['-dispatched_at']

    def get_queryset(self):
        return RepaymentRecord.objects.filter(organization=self.get_org()).select_related(
            'organization', 'batch', 'deduction'
        )


class HealthCheckView(APIView):
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        from django.db import connection
        from django.core.cache import cache
        checks = {}
        try:
            connection.ensure_connection()
            checks['database'] = 'ok'
        except Exception as e:
            checks['database'] = str(e)
        try:
            cache.set('health_check', '1', 5)
            checks['cache'] = 'ok'
        except Exception as e:
            checks['cache'] = str(e)
        all_ok = all(v == 'ok' for v in checks.values())
        return Response(
            {'status': 'healthy' if all_ok else 'degraded', 'checks': checks},
            status=200 if all_ok else 503
        )
