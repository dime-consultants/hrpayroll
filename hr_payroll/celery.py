import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hr_payroll.settings')

app = Celery('hr_payroll')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()