#!/bin/sh
set -e

# Run database migrations for the backend API service (or if RUN_MIGRATIONS=true)
if [ "$1" = "uvicorn" ] || [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "[HITCHINGS] Running database migrations (alembic upgrade head)..."
    alembic upgrade head
fi

echo "[HITCHINGS] Executing: $@"
exec "$@"
