from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('loans', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='loanrequest',
            name='guarantor_id_number',
            field=models.CharField(default='', max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='loanrequest',
            name='guarantor_phone_number',
            field=models.CharField(default='', max_length=20),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name='loanrequest',
            index=models.Index(fields=['guarantor_id_number'], name='loanreq_guarantor_id_idx'),
        ),
        migrations.AddIndex(
            model_name='loanrequest',
            index=models.Index(fields=['guarantor_phone_number'], name='loanreq_guarantor_ph_idx'),
        ),
    ]
