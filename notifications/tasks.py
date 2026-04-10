import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from notifications.email_service import EmailService
from notifications.models import ContractSummaryEmailLog

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2)
def send_contract_summary_followup(self, log_id: str) -> bool:
    """Send a single follow-up email 12 hours after the initial send.

    Guardrails:
    - If followup_sent_at is already set, do nothing.
    - If the initial email is too recent (<12h), do nothing (Celery clock skew safety).
    """
    try:
        log = ContractSummaryEmailLog.objects.get(id=log_id)
    except ContractSummaryEmailLog.DoesNotExist:
        return False

    if log.followup_sent_at is not None:
        return True

    now = timezone.now()
    if log.sent_at and now < (log.sent_at + timedelta(hours=12)):
        logger.info('Follow-up for %s skipped: too early', log_id)
        return False

    subject = (log.subject or '').strip() or 'Contract Reminder'
    followup_subject = f"Follow-up: {subject}" if not subject.lower().startswith('follow-up:') else subject

    html = (
        '<p style="font-family:Arial,sans-serif;">This is an automated follow-up from Lawflow.</p>'
        + (log.body_html or '')
    )

    ok = EmailService()._send_email(
        recipient_email=log.recipient_email,
        subject=followup_subject,
        html_body=html,
        notification_type='contract_summary_followup',
    )

    if ok:
        log.followup_sent_at = now
        log.save(update_fields=['followup_sent_at'])
    return bool(ok)
