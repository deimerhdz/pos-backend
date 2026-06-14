# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

**Start the database (PostgreSQL 16 via Docker):**
```bash
docker-compose up -d
```

**Run the application:**
```bash
uvicorn app.main:app --reload
```

**Database migrations:**
```bash
alembic upgrade head                              # Apply all pending migrations
alembic revision --autogenerate -m "message"     # Generate migration from model changes
alembic downgrade -1                              # Roll back one migration
```

**Tenant management CLI:**
```bash
python -m app.scripts.tenant <command>
```

## Architecture

This is a **multi-tenant POS (Point of Sale) backend** using FastAPI + PostgreSQL with schema-per-tenant isolation.

### Multi-Tenancy Model

- **Shared schema** (`shared`): holds the `tenants` registry table, created once on first startup
- **Tenant schemas**: each tenant gets a dedicated PostgreSQL schema (e.g., `tenant_acme`), created dynamically on tenant creation
- **Tenant resolution**: incoming requests identify their tenant via the HTTP `Host` header; `get_tenant()` in [app/core/db.py](app/core/db.py) looks up the matching row in `shared.tenants`
- **Schema translation**: Alembic and SQLAlchemy use `schema_translate_map={"tenant": tenant.schema}` so all ORM models declared with `schema="tenant"` are automatically scoped to the correct schema at query time

### Request Lifecycle

```
Request → Host header → get_tenant() → get_db(schema) → SQL on tenant schema
```

`get_db()` is a FastAPI dependency that yields a session with the tenant's schema active.

### Key Files

- [app/main.py](app/main.py) — app factory; calls `initialize_database()` on startup, mounts routers
- [app/core/db.py](app/core/db.py) — all multi-tenant database logic: `with_db()`, `get_db()`, `get_tenant()`, `tenant_create()`, `initialize_database()`
- [app/core/models.py](app/core/models.py) — `Base`, `Tenant` model, `TimestampMixin`, `UUIDPrimaryKeyMixin`
- [app/core/config.py](app/core/config.py) — Pydantic `Settings` loaded from `.env`
- [alembic/env.py](alembic/env.py) — Alembic config; the `for_each_tenant_schema` helper runs migrations across all tenant schemas

### Adding New Tenant Models

1. Create a model in [app/models/](app/models/) inheriting from `Base` with `__table_args__ = {"schema": "tenant"}`
2. Add `TimestampMixin` and `UUIDPrimaryKeyMixin` as needed
3. Import the model in [alembic/env.py](alembic/env.py) so autogenerate picks it up
4. Run `alembic revision --autogenerate -m "..."` then `alembic upgrade head`

### Adding New API Endpoints

Routers live under [app/api/v1/](app/api/v1/). The admin router ([app/api/v1/admin/](app/api/v1/admin/)) is the only current router; it requires the `X-Api-Key` header. New tenant-facing routers should use `get_db` as a dependency for automatic tenant isolation.

## Environment

Required `.env` variables:
```
DATABASE_URL=postgresql+psycopg://admin:admin123@localhost:5432/pos_db
JWT_SECRET=<secret>
JWT_ALG=HS256
JWT_EXPIRE_MIN=1440
```

JWT is configured but not yet enforced on endpoints — authentication middleware is not wired up yet.
