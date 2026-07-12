#!/bin/sh
set -e

DB_PATH="${DATA_DIR:-/data}/db.sqlite3"
WORKERS="${WEB_CONCURRENCY:-2}"

# 1) Litestream 복원: 볼륨이 비어 있으면(신규 배포/재해 복구) R2 에서 복원
if [ -n "$LITESTREAM_BUCKET" ]; then
    echo "[entrypoint] litestream restore (if db not exists)..."
    litestream restore -if-db-not-exists -if-replica-exists "$DB_PATH"
fi

# 2) 마이그레이션
echo "[entrypoint] migrate..."
python manage.py migrate --noinput

# 3) 앱 기동 — 복제가 켜져 있으면 litestream 이 uvicorn 을 감독한다
CMD="uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --workers $WORKERS --loop uvloop --ws websockets"

if [ -n "$LITESTREAM_BUCKET" ]; then
    echo "[entrypoint] starting under litestream replicate..."
    exec litestream replicate -exec "$CMD"
else
    echo "[entrypoint] LITESTREAM_BUCKET not set — starting without replication"
    exec $CMD
fi
