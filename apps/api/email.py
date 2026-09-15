"""
apps/api/email.py

Transactional email via Resend (https://resend.com). RESEND_API_KEY must be
set for emails to actually send — if it's blank (e.g. local dev without a
key configured), we log the would-be email instead of raising, since a
password-reset request must still return its normal response either way.
"""
import logging

import resend
from django.conf import settings

log = logging.getLogger(__name__)


def send_password_reset_email(email: str, reset_url: str):
    if not settings.RESEND_API_KEY:
        log.warning(
            'RESEND_API_KEY not set — skipping password reset email to %s (reset_url=%s)',
            email, reset_url,
        )
        return

    resend.api_key = settings.RESEND_API_KEY
    try:
        resend.Emails.send({
            'from':    settings.DEFAULT_FROM_EMAIL,
            'to':      [email],
            'subject': 'Reset your Dime HR Payroll password',
            'html': (
                f'<p>We received a request to reset your Dime HR Payroll password.</p>'
                f'<p><a href="{reset_url}">Click here to choose a new password</a>.</p>'
                f'<p>If you did not request this, you can safely ignore this email.</p>'
            ),
        })
    except Exception:
        log.exception('Failed to send password reset email to %s via Resend', email)
