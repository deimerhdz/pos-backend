#!/usr/bin/env sh
set -e

echo "▶ Inicializando BD (esquema shared + seeds)..."
python -c "from app.core.db import initialize_database; initialize_database()"

echo "▶ Aplicando migraciones pendientes..."
alembic upgrade head

echo "▶ Iniciando API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
