"""Infraestructura compartida para los tests HTTP del envelope de error de
super-admin (spec 068) — no es código de producción.

A diferencia de `auth_fixtures.py`/`plan_fixtures.py` (que invocan las
funciones de endpoint directamente, en proceso), estos tests necesitan
atravesar la app ASGI real: solo así se ejercitan `RequestIdMiddleware` y los
`exception_handler` de `app/core/error_middleware.py` (research.md § 5/§ 7 —
llamar a una función de router en proceso los deja completamente invisibles).
Por eso se usa `starlette.testclient.TestClient` — patrón nuevo en este
paquete, no usado hasta ahora (`plan.md` § Technical Context) — contra una
app mínima propia, no contra `app.main.app`: `app.main.create_app()` exige
Postgres/Redis reales desde su primera línea (`initialize_database()`,
`token_blocklist.ping()` en el lifespan), que este entorno de tests, igual
que el resto del paquete, no tiene ni necesita.
"""
from __future__ import annotations

import uuid
from typing import Optional

from app.characterization_tests import plan_fixtures as fx  # noqa: F401 - fija las env vars de Settings al importarse

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.super_admin.router import router as super_admin_router
from app.core.dependencies import get_current_super_admin, get_shared_db, get_valid_token_data
from app.core.error_middleware import RequestIdMiddleware, register_error_handlers
from app.core.models import Base

#: Igual que `app.main.SUPER_ADMIN_ERROR_PREFIX` — duplicado deliberadamente
#: aquí como constante de solo-test para no importar `app.main` (que exige
#: Postgres/Redis reales al construirse).
SUPER_ADMIN_PREFIX = "/api/v1/super-admin"

#: Ruta de control fuera del prefijo de super-admin (usada por
#: `test_error_middleware_scope.py`): reproduce, con una excepción idéntica,
#: la respuesta plana `{"detail": ...}` que cualquier otro módulo produce hoy
#: vía `get_or_404`, para verificar que el middleware/handlers nuevos no la
#: tocan.
CONTROL_PREFIX = "/api/v1/control"


def new_session() -> Session:
    """Como `plan_fixtures.new_session()`, pero con `check_same_thread=False`.

    `TestClient` despacha cada solicitud a través del *portal* interno de
    Starlette, que corre el ASGI app en un hilo distinto al que ejecuta el
    cuerpo del test — el mismo hilo que crea esta sesión SQLite en memoria.
    SQLite rechaza por defecto usar una conexión desde un hilo distinto al
    que la creó (`sqlite3.ProgrammingError`); ningún test existente de este
    paquete lo necesita porque ninguno pasa por `TestClient` (invocan las
    funciones del router en proceso, mismo hilo). No hace falta relajar esto
    en `plan_fixtures.new_session()` — nada más lo necesita.
    """
    tables = [t for t in Base.metadata.tables.values() if t.name in fx._TABLE_NAMES]
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    conn = engine.connect().execution_options(
        schema_translate_map={"tenant": None, "shared": None}
    )
    Base.metadata.create_all(bind=conn, tables=tables)
    conn.commit()
    return Session(bind=conn)


def build_app(db, *, token_data: Optional[dict] = None) -> FastAPI:
    """App mínima: solo el router de super-admin + la infraestructura de
    errores registrada exactamente como en `app.main.create_app()`.

    `get_shared_db` se sobreescribe para usar la sesión SQLite del test.
    `get_valid_token_data` se sobreescribe solo si se pasa `token_data`
    (evita necesitar Redis real para `token_in_blocklist` — research.md); el
    resto de `get_current_super_admin` corre sin modificar, con su propia
    lógica de autorización real.
    """
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware, path_prefix=SUPER_ADMIN_PREFIX)
    register_error_handlers(app, path_prefix=SUPER_ADMIN_PREFIX)
    app.include_router(super_admin_router, prefix="/api/v1")

    app.dependency_overrides[get_shared_db] = lambda: db
    if token_data is not None:
        async def _fake_valid_token_data():
            return token_data

        app.dependency_overrides[get_valid_token_data] = _fake_valid_token_data

    @app.get(f"{CONTROL_PREFIX}/{{item_id}}")
    def _control_not_found(item_id: str):
        raise StarletteHTTPException(status_code=404, detail="Control not found")

    return app


def super_admin_token_data(user) -> dict:
    """`token_data` mínimo que `get_current_super_admin` acepta como válido."""
    return {"user": {"email": user.email, "is_super_admin": True, "uid": str(user.id)}}


def make_super_admin(db) -> "fx.User":
    """Usuario super-admin real en la sesión SQLite del test: `tenant_id`
    `None` y `active=True`, igual que exige `get_current_super_admin`
    (research.md § 3)."""
    role = fx.make_role(db, name="SUPER_ADMIN")
    user = fx.User(
        id=uuid.uuid4(),
        name="Super Admin de prueba",
        email=f"super-{uuid.uuid4()}@example.com",
        password_hash="x",
        active=True,
        must_change_password=False,
        role_id=role.id,
        tenant_id=None,
    )
    db.add(user)
    db.flush()
    return user
