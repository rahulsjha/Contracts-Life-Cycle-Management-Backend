#!/bin/sh
set -eu

# Optional startup migrations.
# Enable in production via RUN_MIGRATIONS_ON_STARTUP=true.
RUN_MIGRATIONS_ON_STARTUP="${RUN_MIGRATIONS_ON_STARTUP:-}"

if [ "${RUN_MIGRATIONS_ON_STARTUP}" = "true" ] || [ "${RUN_MIGRATIONS_ON_STARTUP}" = "1" ]; then
  echo "[entrypoint] Running migrations..."
  python manage.py migrate --noinput
else
  echo "[entrypoint] Skipping migrations (set RUN_MIGRATIONS_ON_STARTUP=true to enable)"
fi

# Collect static files (best-effort; some deployments don't need it).
echo "[entrypoint] Collecting static files (best-effort)..."
python manage.py collectstatic --noinput || true

echo "[entrypoint] Starting gunicorn..."
exec gunicorn \
  --bind "0.0.0.0:${PORT:-8080}" \
  --workers "${GUNICORN_WORKERS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-180}" \
  --access-logfile - \
  --error-logfile - \
  --log-level "${GUNICORN_LOG_LEVEL:-info}" \
  clm_backend.wsgi:application
