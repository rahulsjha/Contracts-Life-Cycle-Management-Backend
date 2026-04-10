from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ContractSummaryEmailLog',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True)),
                ('contract_id', models.UUIDField(db_index=True)),
                ('kind', models.CharField(max_length=32, choices=[('overdue', 'Overdue Obligation Reminder'), ('renewal', 'Renewal / Expiring Contract Brief')], db_index=True)),
                ('recipient_email', models.EmailField(max_length=254, db_index=True)),
                ('subject', models.CharField(max_length=255, blank=True, default='')),
                ('body_html', models.TextField(blank=True, default='')),
                ('status', models.CharField(max_length=20, choices=[('sent', 'Sent'), ('failed', 'Failed')], default='sent')),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
                ('followup_scheduled_at', models.DateTimeField(null=True, blank=True)),
                ('followup_sent_at', models.DateTimeField(null=True, blank=True)),
            ],
            options={
                'db_table': 'contract_summary_email_logs',
            },
        ),
        migrations.AddIndex(
            model_name='contractsummaryemaillog',
            index=models.Index(fields=['tenant_id', 'contract_id', 'kind', 'recipient_email', 'sent_at'], name='contract_sum_tenant__4a728a_idx'),
        ),
    ]
