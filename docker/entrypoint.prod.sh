#!/bin/sh
set -e

echo "==> Waiting for DB..."
until python -c "
import psycopg, os
try:
    psycopg.connect(
        dbname=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', '5432')
    )
    print('DB connection successful')
except Exception:
    exit(1)
" 2>/dev/null; do
    echo "  retrying..."
    sleep 2
done
echo "==> DB ready."

echo "==> Creating migrations..."
python manage.py makemigrations base organizations payroll repayments --noinput

echo "==> Running migrations..."
python manage.py migrate --noinput

echo "==> Collecting static..."
python manage.py collectstatic --noinput --clear

echo "==> Creating superuser..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
import os
User = get_user_model()
admin_email = os.environ.get('DJANGO_ADMIN_EMAIL', 'admin@hr.dimeapp.co.ke')
if not User.objects.filter(email=admin_email).exists():
    User.objects.create_superuser(
        email=admin_email,
        password=os.environ.get('DJANGO_ADMIN_PASSWORD', 'changeme123!')
    )
    print('Superuser created')
else:
    print('Superuser already exists')
"

echo "==> Syncing organizations..."
python manage.py sync_organizations || echo "Org sync skipped - run manually"

echo "==> Starting Gunicorn on port 8080..."
exec gunicorn hr_payroll.wsgi:application \
    --bind 0.0.0.0:8080 \
    --workers 4 \
    --timeout 120 \
    --keep-alive 5 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --access-logfile - \
    --error-logfile - \
    --log-level info
