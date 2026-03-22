from decimal import Decimal
from django.contrib.auth.models import User
from rest_framework import serializers

from apps.organizations.models import CheckoffOrganizationMirror, HRUser
from apps.payroll.models import PayrollUpload, SalaryDeduction
from apps.repayments.models import RepaymentBatch, RepaymentRecord
from apps.api.validators import normalize_phone, validate_phone


class CheckoffOrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CheckoffOrganizationMirror
        fields = ('id', 'lms_id', 'name', 'code', 'email', 'phone_number',
                  'contact_name', 'is_active', 'date_created')
        read_only_fields = ('id', 'date_created')


class HRUserSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    full_name = serializers.SerializerMethodField()
    organization_name = serializers.CharField(source='organization.name', read_only=True)

    class Meta:
        model = HRUser
        fields = ('id', 'username', 'full_name', 'organization', 'organization_name',
                  'role', 'is_active', 'date_created')
        read_only_fields = ('id', 'date_created')

    def get_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username


class HRUserCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=50)
    last_name = serializers.CharField(max_length=50)
    password = serializers.CharField(min_length=8, write_only=True)
    role = serializers.ChoiceField(choices=HRUser.ROLE_CHOICES)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError('Username already exists.')
        return value

    def create(self, validated_data):
        request = self.context['request']
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            password=validated_data['password'],
        )
        return HRUser.objects.create(
            user=user,
            organization=request.hr_organization,
            role=validated_data['role'],
            created_by=request.user,
        )


class PayrollUploadSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()
    progress_percent = serializers.FloatField(read_only=True)
    success_rows = serializers.IntegerField(read_only=True)

    class Meta:
        model = PayrollUpload
        fields = (
            'id', 'organization', 'organization_name', 'uploaded_by', 'uploaded_by_name',
            'file', 'original_filename', 'status', 'total_rows', 'processed_rows',
            'failed_rows', 'success_rows', 'progress_percent', 'payroll_period',
            'notes', 'error_log', 'celery_task_id', 'date_created', 'date_modified'
        )
        read_only_fields = (
            'id', 'organization', 'uploaded_by', 'status', 'total_rows',
            'processed_rows', 'failed_rows', 'error_log', 'celery_task_id',
            'date_created', 'date_modified'
        )

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return None


class PayrollUploadCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollUpload
        fields = ('file', 'payroll_period', 'notes')

    def validate_file(self, value):
        allowed = ['.xlsx', '.xls', '.csv']
        if not any(value.name.lower().endswith(ext) for ext in allowed):
            raise serializers.ValidationError(
                f'Only {", ".join(allowed)} files are accepted.'
            )
        if value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError('File must be under 10MB.')
        return value

    def create(self, validated_data):
        request = self.context['request']
        upload = PayrollUpload.objects.create(
            organization=request.hr_organization,
            uploaded_by=request.user,
            original_filename=validated_data['file'].name,
            **validated_data
        )
        from apps.repayments.tasks import parse_payroll_upload
        parse_payroll_upload.delay(str(upload.id))
        return upload


class SalaryDeductionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryDeduction
        fields = (
            'id', 'upload', 'organization', 'phone_number', 'employee_name',
            'employee_id', 'amount', 'deduction_date', 'reference', 'status',
            'attempts', 'last_attempt_at', 'lms_response', 'failure_reason',
            'row_number', 'idempotency_key', 'date_created'
        )
        read_only_fields = fields


class RepaymentBatchSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = RepaymentBatch
        fields = (
            'id', 'organization', 'organization_name', 'upload', 'status',
            'total_deductions', 'total_amount', 'successful_count', 'failed_count',
            'skipped_count', 'successful_amount', 'approved_by', 'approved_by_name',
            'approved_at', 'celery_task_id', 'date_created', 'date_modified'
        )
        read_only_fields = fields

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return obj.approved_by.get_full_name() or obj.approved_by.username
        return None


class RepaymentRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = RepaymentRecord
        fields = (
            'id', 'batch', 'deduction', 'organization', 'phone_number',
            'amount_requested', 'amount_sent', 'status', 'attempt_number',
            'lms_response_code', 'lms_response_body', 'failure_reason',
            'duration_ms', 'dispatched_at', 'completed_at'
        )
        read_only_fields = fields


class BatchApproveSerializer(serializers.Serializer):
    confirm = serializers.BooleanField(
        help_text='Set to true to confirm batch approval and trigger dispatch.'
    )

    def validate_confirm(self, value):
        if not value:
            raise serializers.ValidationError('You must confirm approval.')
        return value


class DashboardSummarySerializer(serializers.Serializer):
    organization = serializers.CharField()
    period = serializers.CharField()
    total_uploads = serializers.IntegerField()
    total_deductions = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    successful_amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    pending_batches = serializers.IntegerField()
    failed_deductions = serializers.IntegerField()
