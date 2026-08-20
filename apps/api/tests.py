from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from apps.organizations.models import CheckoffOrganizationMirror, HRUser


User = get_user_model()

URL_LIST = '/api/v1/uploads/'
URL_DETAIL = '/api/v1/uploads/{}/'


def make_csv():
    return SimpleUploadedFile(
        'payroll.csv',
        b'phone,name,amount,date\n'
        b'0712345678,John Doe,5000.00,2026-06-01',
        content_type='text/csv',
    )


class PayrollUploadAPITests(APITestCase):

    def setUp(self):
        self.org = CheckoffOrganizationMirror.objects.create(
            lms_id='lms-test-001',
            name='Test Corp',
            code='TESTCORP',
        )

        self.other_org = CheckoffOrganizationMirror.objects.create(
            lms_id='lms-test-002',
            name='Other Corp',
            code='OTHERCORP',
        )

        self.admin_user, _ = User.objects.create_user(
            email='admin@testcorp.com',
            password='adminpass1',
            first_name='Admin',
            last_name='User',
        )

        HRUser.objects.create(
            user=self.admin_user,
            organization=self.org,
            role='admin',
        )

        self.officer_user, _ = User.objects.create_user(
            email='officer@testcorp.com',
            password='officerpass1',
            first_name='Officer',
            last_name='User',
        )

        HRUser.objects.create(
            user=self.officer_user,
            organization=self.org,
            role='officer',
        )

        self.viewer_user, _ = User.objects.create_user(
            email='viewer@testcorp.com',
            password='viewerpass1',
            first_name='Viewer',
            last_name='User',
        )

        HRUser.objects.create(
            user=self.viewer_user,
            organization=self.org,
            role='viewer',
        )

        self.plain_user, _ = User.objects.create_user(
            email='plain@testcorp.com',
            password='plainpass1',
            first_name='Plain',
            last_name='User',
        )

        self.other_admin, _ = User.objects.create_user(
            email='admin@othercorp.com',
            password='adminpass2',
            first_name='Other',
            last_name='Admin',
        )

        HRUser.objects.create(
            user=self.other_admin,
            organization=self.other_org,
            role='admin',
        )

    # ------------------------------------------------------------------
    # LIST: GET /api/v1/uploads/
    # ------------------------------------------------------------------

    def test_list_unauthenticated_returns_401(self):
        response = self.client.get(URL_LIST)

        self.assertEqual(
            response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_list_no_hr_profile_returns_403(self):
        self.client.force_authenticate(
            user=self.plain_user,
        )

        response = self.client.get(URL_LIST)

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_list_hr_admin_returns_200(self):
        self.client.force_authenticate(
            user=self.admin_user,
        )

        response = self.client.get(URL_LIST)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

    def test_list_hr_officer_returns_200(self):
        self.client.force_authenticate(
            user=self.officer_user,
        )

        response = self.client.get(URL_LIST)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

    def test_list_hr_viewer_returns_200(self):
        self.client.force_authenticate(
            user=self.viewer_user,
        )

        response = self.client.get(URL_LIST)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

    def test_list_scoped_to_own_org(self):
        self.client.force_authenticate(
            user=self.other_admin,
        )

        response = self.client.get(URL_LIST)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data['results'],
            [],
        )

    # ------------------------------------------------------------------
    # CREATE: POST /api/v1/uploads/
    # ------------------------------------------------------------------

    def test_create_admin_returns_202(self):
        self.client.force_authenticate(
            user=self.admin_user,
        )

        response = self.client.post(
            URL_LIST,
            {
                'file': make_csv(),
                'payroll_period': '2026-06-01',
            },
            format='multipart',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_202_ACCEPTED,
        )

        self.assertIn(
            'id',
            response.data,
        )

    def test_create_admin_returns_upload_id(self):
        self.client.force_authenticate(
            user=self.admin_user,
        )

        response = self.client.post(
            URL_LIST,
            {
                'file': make_csv(),
                'payroll_period': '2026-06-01',
            },
            format='multipart',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_202_ACCEPTED,
        )

        self.assertTrue(
            response.data.get('id'),
        )

    def test_create_officer_returns_403(self):
        self.client.force_authenticate(
            user=self.officer_user,
        )

        response = self.client.post(
            URL_LIST,
            {
                'file': make_csv(),
                'payroll_period': '2026-06-01',
            },
            format='multipart',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_create_viewer_returns_403(self):
        self.client.force_authenticate(
            user=self.viewer_user,
        )

        response = self.client.post(
            URL_LIST,
            {
                'file': make_csv(),
                'payroll_period': '2026-06-01',
            },
            format='multipart',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_create_unauthenticated_returns_401(self):
        response = self.client.post(
            URL_LIST,
            {
                'file': make_csv(),
                'payroll_period': '2026-06-01',
            },
            format='multipart',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_create_rejects_non_spreadsheet(self):
        self.client.force_authenticate(
            user=self.admin_user,
        )

        bad_file = SimpleUploadedFile(
            'report.pdf',
            b'%PDF',
            content_type='application/pdf',
        )

        response = self.client.post(
            URL_LIST,
            {
                'file': bad_file,
                'payroll_period': '2026-06-01',
            },
            format='multipart',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    # ------------------------------------------------------------------
    # DETAIL: GET /api/v1/uploads/<pk>/
    # ------------------------------------------------------------------

    def test_detail_owner_org_returns_200(self):
        self.client.force_authenticate(
            user=self.admin_user,
        )

        create_response = self.client.post(
            URL_LIST,
            {
                'file': make_csv(),
                'payroll_period': '2026-06-01',
            },
            format='multipart',
        )

        self.assertEqual(
            create_response.status_code,
            status.HTTP_202_ACCEPTED,
        )

        upload_id = create_response.data['id']

        response = self.client.get(
            URL_DETAIL.format(upload_id),
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            str(response.data['id']),
            str(upload_id),
        )

    def test_detail_different_org_returns_404(self):
        self.client.force_authenticate(
            user=self.admin_user,
        )

        create_response = self.client.post(
            URL_LIST,
            {
                'file': make_csv(),
                'payroll_period': '2026-06-01',
            },
            format='multipart',
        )

        self.assertEqual(
            create_response.status_code,
            status.HTTP_202_ACCEPTED,
        )

        upload_id = create_response.data['id']

        self.client.force_authenticate(
            user=self.other_admin,
        )

        response = self.client.get(
            URL_DETAIL.format(upload_id),
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
