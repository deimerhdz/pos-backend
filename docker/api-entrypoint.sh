#!/usr/bin/env sh
set -e

echo "▶ Inicializando base de datos..."
python -c "from app.core.db import initialize_database; initialize_database()"

echo "▶ Ejecutando migraciones..."
alembic upgrade head

echo "▶ Iniciando API..."
exec "$@"