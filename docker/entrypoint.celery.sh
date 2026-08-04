#!/bin/sh
set -e

echo "==> [Celery] Waiting for DB..."
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
echo "==> [Celery] DB ready."

exec "$@"
