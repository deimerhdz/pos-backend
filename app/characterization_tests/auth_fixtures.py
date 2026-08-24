"""Infraestructura compartida para tests de `auth` — no es código de producción.

Extiende el truco de `schema_translate_map` de `fixtures.py` (mismo patrón,
research.md Decisión 10 de spec 031) para colapsar **también** el schema
`shared` a `None` sobre SQLite en memoria (además de `tenant`) — `auth` vive
enteramente en `shared` (Tenant/Role/User/PasswordResetToken), que la fixture
genérica de `fixtures.py` no materializa.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

# Mismo relleno de variables inertes que `fixtures.py` (Settings exige varias
# sin default) — ningún test de este paquete abre conexión de red real.
for _k, _v in {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost/x",
    "JWT_SECRET": "test",
    "REDIS_URL": "redis://localhost:6379/0",
    "EMAIL_API_URL": "https://example.invalid",
    "MAIL_FROM_NAME": "t",
    "MAIL_FROM": "t@example.invalid",
    "SUPER_ADMIN_NAME": "t",
    "SUPER_ADMIN_EMAIL": "t@example.invalid",
    "SUPER_ADMIN_PASSWORD": "t",
    "R2_ACCOUNT_ID": "x",
    "R2_ACCESS_KEY_ID": "x",
    "R2_SECRET_ACCESS_KEY": "x",
    "R2_BUCKET_NAME": "x",
    "R2_ENDPOINT_URL": "https://example.invalid",
    "R2_PUBLIC_BASE_URL": "https://example.invalid",
}.items():
    os.environ.setdefault(_k, _v)

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.models import Base, PasswordResetToken, Role, Tenant, User
from app.core.utils import generate_passwd_hash

_TABLE_NAMES = ["tenants", "roles", "users", "password_reset_tokens"]


def new_session() -> Session:
    """Sesión SQLAlchemy real, limpia, sobre SQLite en memoria — colapsa tanto
    `tenant` como `shared` al schema por defecto de SQLite."""
    tables = [t for t in Base.metadata.tables.values() if t.name in _TABLE_NAMES]
    engine = create_engine("sqlite:///:memory:")
    conn = engine.connect().execution_options(
        schema_translate_map={"tenant": None, "shared": None}
    )
    Base.metadata.create_all(bind=conn, tables=tables)
    conn.commit()
    return Session(bind=conn)


def _uid() -> uuid.UUID:
    return uuid.uuid4()


def make_tenant(db: Session, **kw) -> Tenant:
    kw.setdefault("name", f"tenant-{uuid.uuid4()}")
    kw.setdefault("schema", f"schema_{uuid.uuid4().hex[:8]}")
    kw.setdefault("host", f"host-{uuid.uuid4().hex[:8]}")
    obj = Tenant(**kw)
    db.add(obj)
    db.flush()
    return obj


def make_role(db: Session, **kw) -> Role:
    kw.setdefault("id", _uid())
    kw.setdefault("name", "CASHIER")
    kw.setdefault("active", True)
    obj = Role(**kw)
    db.add(obj)
    db.flush()
    return obj


def make_user(
    db: Session,
    tenant: Tenant | None = None,
    role: Role | None = None,
    password: str = "contraseña-actual",
    **kw,
) -> User:
    if role is None:
        role = make_role(db)
    kw.setdefault("id", _uid())
    kw.setdefault("name", f"usuario-{uuid.uuid4()}")
    kw.setdefault("email", f"user-{uuid.uuid4()}@example.com")
    kw.setdefault("password_hash", generate_passwd_hash(password))
    kw.setdefault("active", True)
    kw.setdefault("must_change_password", False)
    kw.setdefault("role_id", role.id)
    kw.setdefault("tenant_id", tenant.id if tenant else None)
    obj = User(**kw)
    db.add(obj)
    db.flush()
    return obj


def make_password_reset_token(
    db: Session,
    user: User,
    raw_token: str,
    *,
    issued_at: datetime | None = None,
    expiry_minutes: int = 30,
    **kw,
) -> PasswordResetToken:
    if issued_at is None:
        issued_at = datetime.now(timezone.utc).replace(tzinfo=None)
    kw.setdefault("id", _uid())
    kw.setdefault("user_id", user.id)
    kw.setdefault("token_hash", hashlib.sha256(raw_token.encode("utf-8")).hexdigest())
    kw.setdefault("email_snapshot", user.email)
    kw.setdefault("issued_at", issued_at)
    kw.setdefault("expires_at", issued_at + timedelta(minutes=expiry_minutes))
    obj = PasswordResetToken(**kw)
    db.add(obj)
    db.flush()
    return obj
