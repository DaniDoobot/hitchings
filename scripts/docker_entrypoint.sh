#!/bin/sh
set -e

echo "[HITCHINGS] Running database migrations (alembic upgrade head)..."
alembic upgrade head

echo "[HITCHINGS] Starting FastAPI backend on 0.0.0.0:8000..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
