from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.api.error_handling import ErrorClassification
from apps.organizations.models import CheckoffOrganizationMirror, HRUser
from .models import CustomerRegistration, KYCDocument
from .tasks import register_borrower_task, upload_kyc_documents_task

User = get_user_model()


def _tiny_jpeg(name):
    # 1x1 pixel JPEG — enough to satisfy FileExtensionValidator + storage.
    content = bytes.fromhex(
        'ffd8ffe000104a46494600010100000100010000ffdb004300030202020202'
        '03020202030303030406040404040408060605060909080a0a090809090a0c'
        '0f0c0a0b0e0b09090d110d0e0f101011100a0c12131210130f101010ffc900'
        '0b080001000103011100ffcc000600101005ffda0008010100003f00d2cf20'
        'ffd9'
    )
    return SimpleUploadedFile(name, content, content_type='image/jpeg')


class CustomerRegistrationFlowTests(TestCase):
    def setUp(self):
        self.org = CheckoffOrganizationMirror.objects.create(
            lms_id='org-lms-1', name='APPTIVATE AFRICA', code='APPTIVATE',
        )
        # create_user returns (User, users.HRUser) — unpack and ignore the users-app HRUser
        # (a separate, unused legacy model); the real profile is apps.organizations.HRUser.
        self.user, _ = User.objects.create_user(email='hr@apptivate.africa', password='pass1234')
        HRUser.objects.create(user=self.user, organization=self.org, role='officer')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _submit(self, **overrides):
        payload = {
            'salutation': 'Mr',
            'first_name': 'Joe',
            'last_name': 'Doe',
            'other_name': 'Samson',
            'gender': 'Male',
            'date_of_birth': '1998-03-12',
            'identity_type_name': 'National ID',
            'identity_number': '36789107',
            'phone_number': '254700000000',
            'email': 'joedoe@gmail.com',
            'address': 'Nairobi, Kenya',
            'working_status': 'Employee',
            'country': 'KE',
            'national_id_front': _tiny_jpeg('front.jpg'),
            'national_id_back': _tiny_jpeg('back.jpg'),
            'selfie_photo': _tiny_jpeg('selfie.jpg'),
        }
        for key, value in overrides.items():
            if value is None:
                payload.pop(key, None)
            else:
                payload[key] = value
        return self.client.post('/api/customers/registrations/', payload, format='multipart')

    def test_create_registration_requires_national_id_back(self):
        resp = self._submit(national_id_back=None)
        self.assertEqual(resp.status_code, 400)

    def test_create_registration_success(self):
        resp = self.client.post
        resp = self._submit()
        self.assertEqual(resp.status_code, 201, resp.data)
        registration = CustomerRegistration.objects.get()
        self.assertEqual(registration.status, CustomerRegistration.STATUS_APPROVAL_PENDING)
        self.assertEqual(registration.kyc_documents.count(), 3)
        self.assertEqual(
            set(registration.kyc_documents.values_list('document_type', flat=True)),
            {KYCDocument.DOC_NATIONAL_ID_FRONT, KYCDocument.DOC_NATIONAL_ID_BACK, KYCDocument.DOC_SELFIE},
        )

    def test_duplicate_registration_rejected(self):
        first = self._submit()
        self.assertEqual(first.status_code, 201, first.data)
        second = self._submit(
            national_id_front=_tiny_jpeg('front2.jpg'),
            national_id_back=_tiny_jpeg('back2.jpg'),
            selfie_photo=_tiny_jpeg('selfie2.jpg'),
        )
        self.assertEqual(second.status_code, 400)


class CustomerRegistrationTaskTests(TestCase):
    def setUp(self):
        self.org = CheckoffOrganizationMirror.objects.create(
            lms_id='org-lms-2', name='APPTIVATE AFRICA', code='APPTIVATE2',
        )
        self.registration = CustomerRegistration.objects.create(
            organization=self.org,
            status=CustomerRegistration.STATUS_APPROVED,
            first_name='Joe', last_name='Doe', gender='Male',
            date_of_birth='1998-03-12', identity_number='36789107',
            phone_number='254700000000',
        )
        for doc_type in (KYCDocument.DOC_NATIONAL_ID_FRONT, KYCDocument.DOC_NATIONAL_ID_BACK, KYCDocument.DOC_SELFIE):
            KYCDocument.objects.create(
                registration=self.registration, document_type=doc_type,
                file=_tiny_jpeg(f'{doc_type}.jpg'), status=KYCDocument.STATUS_APPROVED,
            )

    @patch('apps.customers.tasks.upload_kyc_documents_task.delay')
    @patch('apps.api.lms_client.register_borrower')
    def test_register_borrower_task_success_enqueues_kyc_upload(self, mock_register, mock_kyc_delay):
        mock_register.return_value = (
            {'code': '200.001', 'message': 'ok', 'data': {'customer_id': 'cust-uuid-1', 'loan_disk_id': '777'}},
            None,
        )
        register_borrower_task(str(self.registration.id))

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.lms_customer_id, 'cust-uuid-1')
        self.assertEqual(self.registration.status, CustomerRegistration.STATUS_PROCESSING)
        mock_kyc_delay.assert_called_once_with(str(self.registration.id))

    @patch('apps.api.lms_client.register_borrower')
    def test_register_borrower_task_business_failure_marks_failed(self, mock_register):
        mock_register.return_value = (
            {'code': '400.001', 'message': 'Invalid identity number'},
            ErrorClassification.BUSINESS,
        )
        register_borrower_task(str(self.registration.id))

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, CustomerRegistration.STATUS_FAILED)
        self.assertIn('Invalid identity number', self.registration.failure_reason)

    @patch('apps.api.lms_client.upload_kyc_document')
    def test_upload_kyc_documents_task_success_marks_done(self, mock_upload):
        self.registration.lms_customer_id = 'cust-uuid-1'
        self.registration.save(update_fields=['lms_customer_id'])
        mock_upload.return_value = ({'code': '200.001', 'message': 'ok'}, None)

        upload_kyc_documents_task(str(self.registration.id))

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, CustomerRegistration.STATUS_DONE)
        self.assertTrue(
            all(d.status == KYCDocument.STATUS_UPLOADED for d in self.registration.kyc_documents.all())
        )

    @patch('apps.api.lms_client.upload_kyc_document')
    def test_upload_kyc_documents_task_business_failure_marks_partial(self, mock_upload):
        self.registration.lms_customer_id = 'cust-uuid-1'
        self.registration.save(update_fields=['lms_customer_id'])
        mock_upload.return_value = ({'code': '400.001', 'message': 'Bad photo'}, ErrorClassification.BUSINESS)

        upload_kyc_documents_task(str(self.registration.id))

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, CustomerRegistration.STATUS_PARTIAL)
        self.assertTrue(
            all(d.status == KYCDocument.STATUS_FAILED for d in self.registration.kyc_documents.all())
        )
