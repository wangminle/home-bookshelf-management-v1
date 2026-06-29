#!/bin/sh
set -e

cd /app
mkdir -p "${DATA_DIR:-/data}"

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --app-dir .
