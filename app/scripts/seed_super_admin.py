"""Seed script: crea el usuario super admin global.

Este usuario vive en `shared.users` con `tenant_id = NULL` (super admin global,
no asociado a ningún tenant). Es idempotente: si ya existe un usuario con el
email indicado sin tenant asignado, no hace nada.

Uso:
    python -m app.scripts.seed_super_admin
    python -m app.scripts.seed_super_admin --email admin@pos.com --password "Otra123!" --name "Root"

Si no se pasan argumentos, usa SUPER_ADMIN_NAME / SUPER_ADMIN_EMAIL /
SUPER_ADMIN_PASSWORD desde la configuración (.env).
"""
import argparse
import logging

from sqlalchemy import select

from app.core.config import settings
from app.core.db import with_db
from app.core.models import Role, User
from app.core.utils import generate_passwd_hash

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def seed_super_admin(name: str, email: str, password: str) -> User:
    with with_db(None) as db:
        existing = db.execute(
            select(User).where(User.email == email, User.tenant_id.is_(None))
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("El super admin '%s' ya existe, no se creó ningún usuario.", email)
            return existing

        role = db.execute(
            select(Role).where(Role.name == "SUPER_ADMIN")
        ).scalar_one_or_none()
        if role is None:
            raise RuntimeError(
                "El rol 'SUPER_ADMIN' no existe todavía. "
                "Inicializa la base de datos (arranca la app) antes de correr este seed."
            )

        user = User(
            name=name,
            email=email,
            password_hash=generate_passwd_hash(password),
            active=True,
            must_change_password=True,
            role_id=role.id,
            tenant_id=None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("Super admin '%s' creado correctamente.", email)
        return user


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crea el usuario super admin global (sin tenant asignado)."
    )
    parser.add_argument("--name", default=settings.SUPER_ADMIN_NAME)
    parser.add_argument("--email", default=settings.SUPER_ADMIN_EMAIL)
    parser.add_argument("--password", default=settings.SUPER_ADMIN_PASSWORD)
    args = parser.parse_args()

    seed_super_admin(name=args.name, email=args.email, password=args.password)


if __name__ == "__main__":
    main()
