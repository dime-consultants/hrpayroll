"""
python manage.py sync_organizations
python manage.py sync_organizations --dry-run

Pulls CheckoffOrganization records from the Dime LMS and upserts them
into CheckoffOrganizationMirror. Safe to run repeatedly (idempotent).
"""
import logging
from django.core.management.base import BaseCommand
from django.conf import settings

from apps.api.lms_client import _get_session, _headers
from apps.organizations.models import CheckoffOrganizationMirror

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync CheckoffOrganizations from the Dime LMS into the local mirror table.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would change without writing to DB.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        self.stdout.write(self.style.NOTICE(
            f'Syncing orgs from {settings.LMS_BASE_URL} (dry_run={dry_run})'
        ))

        try:
            resp = _get_session().get(
                f'{settings.LMS_BASE_URL}/api/partner/checkoff-organizations/',
                headers=_headers(),
                timeout=settings.LMS_TIMEOUT,
            )
            resp.raise_for_status()
            orgs = resp.json().get('data', [])
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Failed to fetch orgs: {exc}'))
            return

        created_count = updated_count = 0

        for org in orgs:
            lms_id = str(org.get('id', '')).strip()
            name = str(org.get('name', '')).strip()
            code = str(org.get('code', '')).strip()

            if not lms_id or not code:
                self.stdout.write(self.style.WARNING(f'Skipping incomplete record: {org}'))
                continue

            if dry_run:
                self.stdout.write(f'  [DRY RUN] Would upsert: {name} ({code}) lms_id={lms_id}')
                continue

            obj, created = CheckoffOrganizationMirror.objects.update_or_create(
                lms_id=lms_id,
                defaults={
                    'name': name,
                    'code': code,
                    'email': org.get('email') or '',
                    'phone_number': org.get('phone_number') or '',
                    'contact_name': org.get('contact_name') or '',
                    'is_active': True,
                }
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'  Created: {name} ({code})'))
            else:
                updated_count += 1
                self.stdout.write(f'  Updated: {name} ({code})')

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f'\nDone — created={created_count} updated={updated_count}'
            ))
