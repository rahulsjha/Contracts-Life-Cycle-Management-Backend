from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0002_contract_summary_email_log'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='contractsummaryemaillog',
            old_name='contract_sum_tenant__4a728a_idx',
            new_name='contract_su_tenant__c02f48_idx',
        ),
    ]
