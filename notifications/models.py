from django.db import models
import uuid


class ContractSummaryEmailLog(models.Model):
    """Tracks contract reminder/renewal emails for cooldown + follow-ups."""

    STATUS = [
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]

    KIND = [
        ('overdue', 'Overdue Obligation Reminder'),
        ('renewal', 'Renewal / Expiring Contract Brief'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    contract_id = models.UUIDField(db_index=True)
    kind = models.CharField(max_length=32, choices=KIND, db_index=True)
    recipient_email = models.EmailField(db_index=True)
    subject = models.CharField(max_length=255, blank=True, default='')
    body_html = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS, default='sent')

    sent_at = models.DateTimeField(auto_now_add=True)
    followup_scheduled_at = models.DateTimeField(null=True, blank=True)
    followup_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'contract_summary_email_logs'
        indexes = [
            models.Index(fields=['tenant_id', 'contract_id', 'kind', 'recipient_email', 'sent_at']),
        ]

class NotificationModel(models.Model):
    TYPES = [('email', 'Email'), ('sms', 'SMS'), ('in_app', 'In App')]
    STATUS = [('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed')]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant_id = models.UUIDField()
    recipient_id = models.UUIDField()
    notification_type = models.CharField(max_length=20, choices=TYPES)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True)
    
    class Meta:
        db_table = 'notifications'
        app_label = 'notifications'
