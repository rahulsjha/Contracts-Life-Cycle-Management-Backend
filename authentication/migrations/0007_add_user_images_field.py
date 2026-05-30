from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0006_user_profile_avatar_email_change_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='images',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
