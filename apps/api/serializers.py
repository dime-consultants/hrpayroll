from decimal import Decimal
from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()

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
    email = serializers.CharField(source='user.email', read_only=True)
    full_name = serializers.SerializerMethodField()
    organization_name = serializers.CharField(source='organization.name', read_only=True)

    class Meta:
        model = HRUser
        fields = ('id', 'email', 'full_name', 'organization', 'organization_name',
                  'role', 'is_active', 'date_created')
        read_only_fields = ('id', 'date_created')

    def get_full_name(self, obj):
        return obj.user.full_name or obj.user.email


class HRUserCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=50)
    last_name = serializers.CharField(max_length=50)
    password = serializers.CharField(min_length=8, write_only=True)
    role = serializers.ChoiceField(choices=HRUser.ROLE_CHOICES)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def create(self, validated_data):
        request = self.context['request']
        user = User(
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
        )
        user.set_password(validated_data['password'])
        user.save()
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
            return obj.uploaded_by.full_name or obj.uploaded_by.email
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
        if value.size > 50 * 1024 * 1024:
            raise serializers.ValidationError('File must be under 50MB.')
        return value

    def create(self, validated_data):
        request = self.context['request']
        # Processing is not auto-triggered — it stays APPROVAL_PENDING until
        # an admin runs the "Process selected uploads" action in Django admin.
        return PayrollUpload.objects.create(
            organization=request.hr_organization,
            uploaded_by=request.user,
            original_filename=validated_data['file'].name,
            status=PayrollUpload.STATUS_APPROVAL_PENDING,
            **validated_data
        )


class SalaryDeductionSerializer(serializers.ModelSerializer):
    """
    full_name is resolved from the LMS's customer-exclusive-details endpoint
    (see apps.api.lms_client.get_customer_names_bulk), keyed by phone_number,
    and passed in via serializer context as 'customer_names'. This avoids
    maintaining a separate mirrored customer table entirely.

    If the LMS has no exclusive match for this phone number (not found, or
    the customer is shared across multiple checkoff organisations — 403.002),
    we fall back to whatever name string came in on the uploaded spreadsheet
    (employee_name) rather than showing a blank.
    """
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = SalaryDeduction
        fields = (
            'id', 'upload', 'organization', 'phone_number', 'full_name',
            'employee_id', 'amount', 'deduction_date', 'reference', 'status',
            'attempts', 'last_attempt_at', 'lms_response', 'failure_reason',
            'row_number', 'idempotency_key', 'date_created'
        )
        read_only_fields = fields

    def get_full_name(self, obj):
        customer_names = self.context.get('customer_names', {})
        customer = customer_names.get(obj.phone_number)
        if customer and customer.get('full_name'):
            return customer['full_name']
        return obj.employee_name


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
            return obj.approved_by.full_name or obj.approved_by.email
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