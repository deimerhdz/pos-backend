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
- **Tenant resolution**: incoming requests identify their tenant via the `x-tenant-host` header; `get_tenant()` in [app/core/db.py](app/core/db.py) looks up the matching row in `shared.tenants`
- **Schema translation**: Alembic and SQLAlchemy use `schema_translate_map={"tenant": tenant.schema}` so all ORM models declared with `schema="tenant"` are automatically scoped to the correct schema at query time
- **Identity lives in `shared`**: `users`, `roles` and `tenants` are all in the `shared` schema (see [app/core/models.py](app/core/models.py)). A "tenant user" is a `shared.users` row tagged with `tenant_id`; `tenant_id IS NULL` = the global super admin. Roles seeded: `SUPER_ADMIN`, `ADMIN`, `CASHIER`.
- **User reference from tenant records** (important): tenant-schema tables that record *who acted* (`cash_shifts`, `cash_movements`, `sales`, `customer_orders`, `inventory_movements`, `purchases`) store `user_id` as a **plain UUID soft reference** to `shared.users.id` — **no cross-schema `ForeignKey`** (it would fight `schema_translate_map`). The acting user comes from `get_current_user().id`; historical records (`sales`, `cash_shifts`) also snapshot `user_name`.

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

### Domain model (heladería, modelo simple)

The tenant schema follows a flat relational model tuned for an ice-cream shop MVP (adapted from `schema.sql`). **No batches/FEFO/reservations** — inventory is a single `current_stock` number per item.

- **Catálogo**: `categories` → `products` (`preparation_type`: prepared/packaged) → `product_variants` (price lives here) + `recipe_items` (BOM: variant → inventory item). Menu options via `option_groups`/`options` (a flavor can link an `inventory_item` to deduct) assigned to products through `product_option_groups`. Units in `unit_measures`.
- **Inventario**: `inventory_items` (single stock), `inventory_movements` (kardex: in/out/adjustment), `suppliers`, `purchases`/`purchase_items` (receiving adds stock).
- **Órdenes/QR**: `dining_tables` (`qr_token`), `dining_sessions` (anonymous customer name, one open per table), `customer_orders` + `order_items` + `order_item_options`.
- **Caja**: `cash_registers`, `cash_shifts` (one open per register), `cash_movements`, `cash_count_denominations`.
- **Ventas**: `sales` + `sale_items` (immutable `description` + `options` JSONB snapshot) + `payments`/`payment_methods`.

**Conventions**: UUID PKs (`UUIDPrimaryKeyMixin`); enums are `String` + `CheckConstraint` (not native PG enums); partial-unique invariants use `Index(..., postgresql_where=text(...))`. **Business logic lives in Python services, not DB triggers/views** — inventory deduction on sale ([app/api/v1/sales/consumption.py](app/api/v1/sales/consumption.py)), stock movements ([app/api/v1/inventory/stock.py](app/api/v1/inventory/stock.py)), and cash reconciliation ([app/api/v1/cash/service.py](app/api/v1/cash/service.py)) replace the triggers/views of `schema.sql`. This is required because tenant tables are created from SQLAlchemy metadata (`get_tenant_specific_metadata().create_all()`), which does not know about triggers/views.

### Adding New API Endpoints

Routers live under [app/api/v1/](app/api/v1/): `auth`, `admin`/`super_admin` (JWT super-admin), `users` (tenant admin), `categories`, `unit_measures`, `products`, `catalog` (variants/recipes/options), `inventory`, `menu` (public QR menu), `orders`, `cash`, `sales`. Tenant-facing routers use `get_db` for automatic tenant isolation and `get_current_user`/`require_tenant_admin` ([app/core/dependencies.py](app/core/dependencies.py)) for auth. Reuse `get_or_404`/`ensure_unique` ([app/core/crud.py](app/core/crud.py)) and `paginate` ([app/core/pagination.py](app/core/pagination.py)).

## Environment

Required `.env` variables:
```
DATABASE_URL=postgresql+psycopg://admin:admin123@localhost:5432/pos_db
JWT_SECRET=<secret>
JWT_ALG=HS256
JWT_EXPIRE_MIN=1440
```

JWT auth is wired: `/api/v1/auth/login` resolves the tenant by `x-tenant-host`, verifies against `shared.users`, and issues access/refresh tokens (blocklist in Redis).
