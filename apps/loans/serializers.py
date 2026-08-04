from rest_framework import serializers
from .models import LoanRequest, LoanRequestBatch, LoanRequestUpload


class LoanRequestUploadSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    uploaded_by_name  = serializers.SerializerMethodField()
    progress_percent  = serializers.FloatField(read_only=True)

    class Meta:
        model  = LoanRequestUpload
        fields = (
            'id', 'organization', 'organization_name',
            'uploaded_by', 'uploaded_by_name',
            'original_filename', 'status', 'loan_period',
            'total_rows', 'processed_rows', 'failed_rows',
            'eligible_rows', 'ineligible_rows',
            'progress_percent', 'error_log', 'notes',
            'date_created', 'date_modified',
        )
        read_only_fields = (
            'id', 'status', 'total_rows', 'processed_rows', 'failed_rows',
            'eligible_rows', 'ineligible_rows', 'error_log',
            'date_created', 'date_modified',
        )

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.full_name() or obj.uploaded_by.email
        return None


class LoanRequestUploadCreateSerializer(serializers.ModelSerializer):
    """Used for POST /loan-request-uploads/ — file + loan_period only."""

    class Meta:
        model  = LoanRequestUpload
        fields = ('file', 'loan_period', 'notes')

    def validate_file(self, value):
        name = value.name.lower()
        if not (name.endswith('.xlsx') or name.endswith('.xls')):
            raise serializers.ValidationError('Only .xlsx and .xls files are accepted.')
        if value.size > 10 * 1024 * 1024:  # 10 MB hard cap
            raise serializers.ValidationError('File too large. Maximum size is 10 MB.')
        return value

    def create(self, validated_data):
        request = self.context['request']
        from apps.organizations.models import CheckoffOrganizationMirror
        try:
            hr_profile = request.user.hr_profile
        except Exception:
            raise serializers.ValidationError('User has no HR profile.')

        upload = LoanRequestUpload.objects.create(
            organization      = hr_profile.organization,
            uploaded_by       = request.user,
            file              = validated_data['file'],
            original_filename = validated_data['file'].name,
            loan_period       = validated_data['loan_period'],
            notes             = validated_data.get('notes', ''),
            status            = LoanRequestUpload.STATUS_APPROVAL_PENDING,
        )
        return upload


class LoanRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model  = LoanRequest
        fields = (
            'id', 'phone_number', 'employee_name', 'employee_id',
            'requested_amount', 'accessible_loan_limit', 'existing_loan_balance',
            'status', 'ineligibility_reason',
            'lms_loan_id', 'failure_reason', 'attempts',
            'row_number', 'date_created',
        )
        read_only_fields = fields


class LoanRequestBatchSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)

    class Meta:
        model  = LoanRequestBatch
        fields = (
            'id', 'organization', 'organization_name', 'upload',
            'status', 'total_requests', 'total_amount',
            'eligible_count', 'ineligible_count',
            'successful_count', 'failed_count', 'skipped_count',
            'successful_amount', 'approved_by', 'approved_at',
            'date_created', 'date_modified',
        )
        read_only_fields = fields


# ── Polling serializer ────────────────────────────────────────

class LoanUploadStatusSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for the polling endpoint.
    Returns just enough to update the UI without a full object fetch.
    """
    progress_percent  = serializers.FloatField(read_only=True)
    batch_status      = serializers.SerializerMethodField()
    batch_id          = serializers.SerializerMethodField()

    class Meta:
        model  = LoanRequestUpload
        fields = (
            'id', 'status', 'progress_percent',
            'total_rows', 'processed_rows', 'failed_rows',
            'eligible_rows', 'ineligible_rows',
            'batch_status', 'batch_id',
        )

    def get_batch_status(self, obj):
        try:
            return obj.loan_request_batch.status
        except Exception:
            return None

    def get_batch_id(self, obj):
        try:
            return str(obj.loan_request_batch.id)
        except Exception:
            return None