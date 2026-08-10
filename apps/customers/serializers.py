from django.db import transaction
from rest_framework import serializers

from .models import CustomerRegistration, KYCDocument


class KYCDocumentSerializer(serializers.ModelSerializer):
    document_type_display = serializers.CharField(source='get_document_type_display', read_only=True)

    class Meta:
        model  = KYCDocument
        fields = (
            'id', 'document_type', 'document_type_display', 'file',
            'status', 'failure_reason', 'uploaded_at', 'date_created',
        )
        read_only_fields = fields


class CustomerRegistrationSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    submitted_by_name = serializers.SerializerMethodField()
    kyc_documents      = KYCDocumentSerializer(many=True, read_only=True)

    class Meta:
        model  = CustomerRegistration
        fields = (
            'id', 'organization', 'organization_name',
            'submitted_by', 'submitted_by_name',
            'salutation', 'first_name', 'last_name', 'other_name',
            'gender', 'date_of_birth', 'identity_type_name', 'identity_number',
            'phone_number', 'email', 'address', 'working_status', 'country',
            'borrower_type', 'status', 'lms_customer_id', 'lms_loan_disk_id',
            'failure_reason', 'notes', 'kyc_documents',
            'date_created', 'date_modified',
        )
        read_only_fields = fields

    def get_submitted_by_name(self, obj):
        if obj.submitted_by:
            return obj.submitted_by.full_name or obj.submitted_by.email
        return None


class CustomerRegistrationCreateSerializer(serializers.ModelSerializer):
    """Used for POST /registrations/ — borrower fields + KYC photos, multipart."""

    national_id_front = serializers.FileField(required=False, write_only=True)
    national_id_back  = serializers.FileField(required=False, write_only=True)
    passport_photo    = serializers.FileField(required=False, write_only=True)
    selfie_photo      = serializers.FileField(write_only=True)

    class Meta:
        model  = CustomerRegistration
        fields = (
            'salutation', 'first_name', 'last_name', 'other_name',
            'gender', 'date_of_birth', 'identity_type_name', 'identity_number',
            'phone_number', 'email', 'address', 'working_status', 'country',
            'notes',
            'national_id_front', 'national_id_back', 'passport_photo', 'selfie_photo',
        )

    def validate(self, attrs):
        identity_type = attrs.get('identity_type_name') or 'National ID'
        if identity_type == 'National ID':
            if not attrs.get('national_id_front') or not attrs.get('national_id_back'):
                raise serializers.ValidationError(
                    'Both National ID front and back photos are required.'
                )
        elif identity_type == 'Passport':
            if not attrs.get('passport_photo'):
                raise serializers.ValidationError('Passport photo is required.')
        return attrs

    def create(self, validated_data):
        request = self.context['request']
        try:
            hr_profile = request.user.hr_profile
        except Exception:
            raise serializers.ValidationError('User has no HR profile.')

        national_id_front = validated_data.pop('national_id_front', None)
        national_id_back  = validated_data.pop('national_id_back', None)
        passport_photo     = validated_data.pop('passport_photo', None)
        selfie_photo        = validated_data.pop('selfie_photo')

        registration = CustomerRegistration(
            organization = hr_profile.organization,
            submitted_by = request.user,
            status       = CustomerRegistration.STATUS_APPROVAL_PENDING,
            **validated_data,
        )
        registration.idempotency_key = registration.build_idempotency_key()

        if CustomerRegistration.objects.filter(idempotency_key=registration.idempotency_key).exists():
            raise serializers.ValidationError(
                'A registration for this identity number and phone number already '
                'exists for your organisation.'
            )

        with transaction.atomic():
            registration.save()

            if national_id_front:
                KYCDocument.objects.create(
                    registration=registration, document_type=KYCDocument.DOC_NATIONAL_ID_FRONT, file=national_id_front,
                )
            if national_id_back:
                KYCDocument.objects.create(
                    registration=registration, document_type=KYCDocument.DOC_NATIONAL_ID_BACK, file=national_id_back,
                )
            if passport_photo:
                KYCDocument.objects.create(
                    registration=registration, document_type=KYCDocument.DOC_PASSPORT, file=passport_photo,
                )
            KYCDocument.objects.create(
                registration=registration, document_type=KYCDocument.DOC_SELFIE, file=selfie_photo,
            )

        return registration


class CustomerRegistrationStatusSerializer(serializers.ModelSerializer):
    """Lightweight polling serializer — mirrors LoanUploadStatusSerializer."""

    kyc_documents = KYCDocumentSerializer(many=True, read_only=True)

    class Meta:
        model  = CustomerRegistration
        fields = ('id', 'status', 'failure_reason', 'kyc_documents')
