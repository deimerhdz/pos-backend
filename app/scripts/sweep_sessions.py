"""Barrido manual de sesiones de mesa huérfanas.

    python -m app.scripts.sweep_sessions

Hace lo mismo que el job periódico del `lifespan`, pero a demanda. Útil para
correrlo por cron si se prefiere no depender de APScheduler dentro del proceso web,
o para forzar una limpieza sin esperar al siguiente ciclo.
"""
import logging

from app.core.config import settings
from app.core.scheduler import sweep_orphan_sessions


def main() -> None:
    logging.basicConfig(level=logging.INFO, force=True)
    print(
        f"Cerrando sesiones de mesa con más de {settings.TABLE_SESSION_MAX_HOURS} h abiertas…"
    )
    n = sweep_orphan_sessions()
    print(f"Sesiones cerradas: {n}")


if __name__ == "__main__":
    main()
