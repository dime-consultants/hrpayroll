FROM python:3.12-slim AS builder

WORKDIR /usr/src/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput --clear


FROM python:3.12-slim

WORKDIR /usr/src/app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY --from=builder /usr/src/app /usr/src/app

COPY --from=builder /usr/src/app/staticfiles /usr/src/app/staticfiles

RUN useradd -m -r appuser && \
    chown -R appuser:appuser /usr/src/app && \
    chmod -R 755 /usr/src/app/staticfiles

USER appuser

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate && \
                  daphne -b 0.0.0.0 -p 8000 config.asgi:application"]
