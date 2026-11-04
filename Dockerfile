FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt && \
    (pip install --no-cache-dir --only-binary=:all: cryptg || true)

COPY . .

# Persistent data volume (Backup Browser index / cache)
VOLUME ["/data"]
RUN mkdir -p /data /tmp/tg_dl /tmp/tgcache /tmp/tgup /var/tmp/tg_cache && \
    chmod -R 777 /data /tmp/tg_dl /tmp/tgcache /tmp/tgup /var/tmp/tg_cache

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('PORT','8080')+'/healthz',timeout=4)" || exit 1

CMD ["python", "app.py"]
