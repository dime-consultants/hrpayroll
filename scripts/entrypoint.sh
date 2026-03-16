#!/usr/bin/env bash
set -e

echo "==> Waiting for PostgreSQL..."
until python -c "
import psycopg2, os
psycopg2.connect(
    dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'],
    password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'],
    port=os.environ.get('DB_PORT', 5432),
)
" 2>/dev/null; do
  echo "   Not ready — retrying in 2s..."
  sleep 2
done
echo "   PostgreSQL ready."

echo "==> Waiting for Redis..."
until python -c "
import redis, os
redis.from_url(os.environ.get('REDIS_URL', 'redis://redis:6379/0')).ping()
" 2>/dev/null; do
  echo "   Not ready — retrying in 2s..."
  sleep 2
done
echo "   Redis ready."

echo "==> Running migrations..."
python manage.py migrate --noinput

echo "==> Collecting static files..."
python manage.py collectstatic --noinput --clear

echo "==> Creating superuser if not exists..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
import os
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser(
        username='admin',
        email=os.environ.get('DJANGO_ADMIN_EMAIL', 'admin@hr.dime.africa'),
        password=os.environ.get('DJANGO_ADMIN_PASSWORD', 'changeme123!')
    )
    print('Superuser created')
else:
    print('Superuser already exists')
"

echo "==> Starting..."
exec "$@"
