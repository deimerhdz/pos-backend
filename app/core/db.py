import logging
import uuid
from datetime import datetime, timezone

from contextlib import contextmanager
from fastapi import Depends,Request,HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import create_engine,MetaData,schema as sch,inspect,text

from typing import Optional

from alembic import command
from alembic import script
from alembic.config import Config
from alembic.runtime.migration import MigrationContext

import bcrypt

from app.core.config import settings
from app.core.models import Tenant, User, convention, Base
import app.models  # registra modelos tenant en Base.metadata
from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError

alembic_config = Config("alembic.ini")
engine = create_engine(settings.DATABASE_URL,echo=True,future=True)
logger = logging.getLogger(__name__)

@contextmanager
def with_db(tenant_schema: Optional[str]):
    if tenant_schema:
        schema_translate_map = dict(tenant=tenant_schema)
    else:
        schema_translate_map = None
    connectable = engine.execution_options(schema_translate_map=schema_translate_map)

    try:
        db = Session(autocommit=False, autoflush=False, bind=connectable)
        yield db
    finally:
        db.close()
        
        
def tenant_create(name: str, schema: str, host: str, admin_name: str, admin_email: str, admin_password: str) -> None:
    with with_db(schema) as db:
        try:
            context = MigrationContext.configure(db.connection())
            SCRIPT = script.ScriptDirectory.from_config(alembic_config)
            if context.get_current_revision() != SCRIPT.get_current_head():
                raise RuntimeError(
                    "Database is not up-to-date. Execute migrations before adding new tenants."
                )
            tenant = Tenant(
                name=name,
                host=host,
                schema=schema,
            )
            db.add(tenant)
            db.flush()

            db.execute(sch.CreateSchema(schema))
            get_tenant_specific_metadata().create_all(bind=db.connection())

            admin_role = db.execute(
                text("SELECT id FROM shared.roles WHERE name = 'ADMIN' LIMIT 1")
            ).fetchone()

            if admin_role is None:
                raise RuntimeError("ADMIN role not found. Run migrations first.")

            password_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()
            user = User(
                name=admin_name,
                email=admin_email,
                password_hash=password_hash,
                role_id=admin_role[0],
                tenant_id=tenant.id,
                must_change_password=True,
            )
            db.add(user)

            db.commit()
       
        except IntegrityError as e:
            db.rollback()
            if isinstance(e.orig, UniqueViolation):
                logger.warning("Intento de crear tenant duplicado: schema='%s'", schema)
                raise HTTPException(status_code=409, detail=f"El tenant '{schema}' ya existe")
            logger.exception("IntegrityError inesperado durante tenant_create")
            raise HTTPException(status_code=500, detail="Internal server error")
        
        except Exception as e:
            db.rollback()
            logger.exception("Unexpected error creating tenant"+str(e))
            raise HTTPException(
                status_code=500,
                detail="Internal server error"
            )
    


def get_shared_metadata():
    """identify all shared tables we need to create
    Returns:
        Metadata: the information for specific table 
    """
    meta = MetaData(
        naming_convention=convention
    )
    for table in Base.metadata.tables.values():
        if table.schema != "tenant":
            table.to_metadata(meta)

    return meta


def get_tenant_specific_metadata():
    """identify all the tables we need to create
    Returns:
        Metadata: the information for specific tenant 
    """
    meta = MetaData(schema="tenant")

    for table in Base.metadata.tables.values():
       
        if table.schema == "tenant":
            table.to_metadata(meta)
    return meta



def get_tenant(req: Request) -> Tenant:
    logger.info(f"Obteniendo tenant para host: {req.headers['host']}")
    host_without_port = req.headers["x-tenant-host"].split(":", 1)[0]

    with with_db(None) as db:
        tenant = db.query(Tenant).filter(Tenant.host == host_without_port).one_or_none()
    logger.info(f"Host recibido: {host_without_port}, Tenant encontrado: {tenant.name if tenant else 'None'}")
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Tenant not found for host '{host_without_port}'")

    return tenant

def resolve_tenant_by_id(tenant_id: int) -> Tenant:
    """Gemelo de get_tenant() pero resolviendo por id en vez de host. Lo usa el
    flujo público de QR/sesión, donde el tenant viaja firmado dentro del token
    (claim `t`) y no hay header x-tenant-host."""
    with with_db(None) as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def get_db(tenant: Tenant = Depends(get_tenant)):
    logger.info(f"Obteniendo DB para tenant: {tenant.name} (schema: {tenant.schema})")
    with with_db(tenant.schema) as db:
        yield db


ROLE_NAMES = ("SUPER_ADMIN", "ADMIN", "CASHIER")


def _seed_shared_data(db):
    """Seed base data (roles + super admin) into the shared schema.

    Runs during first-time initialization, right after the shared tables are
    created and before Alembic is stamped to head. Idempotent by name/email so
    it is safe if the shared tables already hold some rows.
    """
    now = datetime.now(timezone.utc)

    existing_roles = {
        row[0] for row in db.execute(text("SELECT name FROM shared.roles")).fetchall()
    }
    for name in ROLE_NAMES:
        if name in existing_roles:
            continue
        db.execute(
            text(
                "INSERT INTO shared.roles (id, name, active, created_at) "
                "VALUES (:id, :name, true, :created_at)"
            ),
            {"id": uuid.uuid4(), "name": name, "created_at": now},
        )

    super_admin_exists = db.execute(
        text("SELECT 1 FROM shared.users WHERE email = :email"),
        {"email": settings.SUPER_ADMIN_EMAIL},
    ).first()
    if super_admin_exists:
        logger.info("shared base data already present")
        return

    role_id = db.execute(
        text("SELECT id FROM shared.roles WHERE name = 'SUPER_ADMIN' LIMIT 1")
    ).scalar_one()

    password_hash = bcrypt.hashpw(
        settings.SUPER_ADMIN_PASSWORD.encode(), bcrypt.gensalt()
    ).decode()

    db.execute(
        text(
            "INSERT INTO shared.users "
            "(id, name, email, password_hash, active, must_change_password, role_id, tenant_id, created_at) "
            "VALUES (:id, :name, :email, :password_hash, true, true, :role_id, NULL, :created_at)"
        ),
        {
            "id": uuid.uuid4(),
            "name": settings.SUPER_ADMIN_NAME,
            "email": settings.SUPER_ADMIN_EMAIL,
            "password_hash": password_hash,
            "role_id": role_id,
            "created_at": now,
        },
    )
    logger.info("seeded roles and super admin user")


def initialize_database():

    with engine.begin() as db:
        context = MigrationContext.configure(db)
        if context.get_current_revision() is not None:
            logger.info("Database already exists.")
            return
        logger.info("creating schema 'shared'...")
        db.execute(sch.CreateSchema("shared"))

        logger.info("creating shared tables...")
        get_shared_metadata().create_all(bind=db)
        logger.info("seeding shared base data...")
        _seed_shared_data(db)
        logger.info("register versión in Alembic...")
        alembic_config.attributes["connection"] = db
        command.stamp(alembic_config, "head", purge=True)
        logger.info("The database has been initialized successfully.")
        

    