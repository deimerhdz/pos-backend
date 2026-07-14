---
name: reset-baseline
description: Reset the dev database and regenerate a single Alembic baseline migration from the current SQLAlchemy models. Use for a greenfield reset when there is no production data to preserve, or after large model changes that make incremental migrations messy.
---

# Reset baseline (greenfield)

Recrea la base de datos de desarrollo y colapsa el historial de migraciones en un
único baseline que refleja los modelos actuales. **Destructivo**: solo para dev sin
data que preservar.

## Contexto del bootstrap (importante)
- `initialize_database()` crea el schema `shared` + siembra roles/super-admin + `alembic stamp head` (NO corre migraciones).
- `tenant_create()` crea el schema del tenant con `get_tenant_specific_metadata().create_all()` (metadata, no migraciones).
- Las migraciones usan `@for_each_tenant_schema` (plantilla `alembic/script.py.mako`) y aplican a `tenant_default` + todos los tenants existentes.
- Las tablas `shared` las gestiona `initialize_database`, **no** las migraciones. Por eso el baseline debe generarse con `shared` ya presente para que autogenerate lo omita (si no, quedaría dentro del loop per-tenant y rompería en la 2ª iteración).

## Pasos

1. **Borra las migraciones viejas**: `rm -f alembic/versions/*.py`

2. **Prepara la reflexión** (shared presente, tenant_default vacío):
```python
from app.core.db import engine, get_shared_metadata
from sqlalchemy import text, schema as sch
with engine.begin() as c:
    for s in ['tenant_default', 'shared']:  # + schemas de tenants dev
        c.execute(text(f'DROP SCHEMA IF EXISTS {s} CASCADE'))
    c.execute(text('DROP TABLE IF EXISTS public.alembic_version CASCADE'))
    c.execute(sch.CreateSchema('shared'))
    get_shared_metadata().create_all(bind=c)   # shared presente => autogen lo omite
    c.execute(sch.CreateSchema('tenant_default'))
```

3. **Genera el baseline**: `alembic revision --autogenerate -m "baseline"`
   - Verifica: solo tablas tenant, envueltas en `@for_each_tenant_schema`, `schema=schema`. Ninguna tabla `shared`.

4. **Bootstrap real limpio**:
```python
from app.core.db import engine, initialize_database, tenant_create
from sqlalchemy import text
with engine.begin() as c:            # deja la DB prístina
    for s in ['tenant_default','shared']:
        c.execute(text(f'DROP SCHEMA IF EXISTS {s} CASCADE'))
    c.execute(text('DROP TABLE IF EXISTS public.alembic_version CASCADE'))
initialize_database()                # crea shared + siembra + stampa head
tenant_create(name=..., schema=..., host=..., admin_name=..., admin_email=..., admin_password=...)
```

5. **Verifica**: el schema del tenant tiene todas las tablas; `shared` tiene roles/tenants/users; `alembic current` == head.

Nota: pon `engine.echo=False` en scripts para logs limpios.
