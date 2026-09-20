#!/usr/bin/env bash
set -euo pipefail

cd /app
export PATH="/app/.venv/bin:$PATH"
export PYTHONPATH="${PYTHONPATH:-/app}"

: "${KEEN_DATABASE_URL:?KEEN_DATABASE_URL is required}"

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

echo "[keen] starting api..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
