#!/usr/bin/env bash
set -euo pipefail

cd /app
export PATH="/app/.venv/bin:$PATH"
export PYTHONPATH="${PYTHONPATH:-/app}"

: "${KEEN_DATABASE_URL:?KEEN_DATABASE_URL is required}"
: "${KEEN_PUBLIC_BASE_URL:?KEEN_PUBLIC_BASE_URL is required: set it to the browser-facing origin, for example https://keen.example.com}"

echo "[keen] waiting for database..."
python - <<'PY'
import os, time
import sqlalchemy as sa

url = os.environ["KEEN_DATABASE_URL"]
engine = sa.create_engine(url, pool_pre_ping=True)

deadline = time.time() + 60
while True:
    try:
        with engine.connect() as c:
            c.execute(sa.text("SELECT 1"))
        print("[keen] database is ready")
        break
    except Exception:
        if time.time() > deadline:
            raise
        time.sleep(1)
PY

echo "[keen] running migrations..."
alembic upgrade head

workers="${KEEN_API_WORKERS:-1}"
case "$workers" in
  [1-9]|[12][0-9]|3[0-2]) ;;
  *) echo "[keen] KEEN_API_WORKERS must be a whole number from 1 to 32" >&2; exit 1 ;;
esac

echo "[keen] starting api with $workers worker process(es)..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers "$workers" --no-proxy-headers --ws-max-size 1024 --ws-max-queue 4
