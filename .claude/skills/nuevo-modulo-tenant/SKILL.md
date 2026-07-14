---
name: nuevo-modulo-tenant
description: Scaffold a new tenant-scoped API module (model + schemas + service + router) following this repo's conventions. Use when adding a new CRUD resource to the POS backend under app/api/v1/ and a matching model under app/models/.
---

# Nuevo módulo tenant

Crea un módulo CRUD tenant-scoped consistente con el resto del backend.

## Convenciones obligatorias (este repo)

**Modelo** — `app/models/<recurso>.py`:
- Hereda de `Base` (+ `UUIDPrimaryKeyMixin`, `TimestampMixin` si aplica) desde `app.core.models`.
- `__table_args__ = ({"schema": "tenant"},)` (o con constraints antes del dict).
- Enums = `String` + `CheckConstraint` (NO enums nativos de PG). Ej: `type IN ('a','b')`.
- FKs a otras tablas tenant: `ForeignKey("otra_tabla.id")` (sin schema; el translate lo resuelve).
- Referencia al usuario actor: `user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True))` **sin ForeignKey** (soft ref a `shared.users.id`); para históricos añade `user_name` snapshot.
- Invariantes de unicidad parcial: `Index("idx_x", "col", unique=True, postgresql_where=text("status='open'"))`.
- Registra el modelo en `app/models/__init__.py`.

**Schemas** — `app/api/v1/<recurso>/schemas.py`: Pydantic v2, `Create`/`Update`/`Response`, `model_config = ConfigDict(from_attributes=True)` en los Response.

**Service** (si hay lógica) — `app/api/v1/<recurso>/service.py`: dueño de la transacción (`db.commit()/rollback()`), reusa `get_or_404` de `app.core.crud`.

**Router** — `app/api/v1/<recurso>/router.py`:
- `router = APIRouter(prefix="/<recurso>", tags=["<recurso>"])`.
- Dependencias: `db: Session = Depends(get_db)` (aislamiento por tenant) y auth con `get_current_user` (lectura) o `require_tenant_admin` (escritura) desde `app.core.dependencies`.
- Reusa `get_or_404`, `ensure_unique` (`app.core.crud`) y `Page`/`paginate` (`app.core.pagination`).
- Para el actor: `user: User = Depends(get_current_user)` y persiste `user.id` / `user.name`.

**Montaje**: importa y `app.include_router(<recurso>_router, prefix="/api/v1")` en `app/main.py`.

## Pasos
1. Escribe el modelo y regístralo en `app/models/__init__.py`.
2. Genera migración: `alembic revision --autogenerate -m "<recurso>"` y revísala (usa `@for_each_tenant_schema`; NO debe crear tablas `shared`).
3. `alembic upgrade head` (afecta `tenant_default` + tenants existentes) — los tenants nuevos toman las tablas del metadata en `tenant_create()`.
4. Escribe schemas → service → router → móntalo en `main.py`.
5. Verifica importando la app y ejercitando el flujo (ver skill `reset-baseline` para un entorno limpio).
