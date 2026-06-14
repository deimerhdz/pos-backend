from logging.config import fileConfig
import os
from sqlalchemy import engine_from_config
from sqlalchemy import pool,MetaData
from dotenv import load_dotenv

from alembic import context
from app.core.db import Base,engine,convention

   # ajusta el path a tu estructura
from app.core.models import Tenant  
import app.models # ajusta el path a tu estructura
   # ajusta el path a tu estructura
# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
# load_dotenv()
# DATABASE_URL= os.getenv("DATABASE_URL")
# from app.models.user import User
# from app.core.tenant import Tenant

target_metadata = Base.metadata


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True, 
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    translated = MetaData(naming_convention=convention)

    def translate_schema(table, to_schema, constraint, referred_schema):
        # pylint: disable=unused-argument
        return to_schema

    for table in Base.metadata.tables.values():
        table.tometadata(
            translated,
            schema="tenant_default" if table.schema == "tenant" else table.schema,
            referred_schema_fn=translate_schema,
        )

    # esquemas que el metadata realmente gestiona (tenant_default + shared, etc.)
    managed_schemas = {t.schema for t in translated.tables.values()} | {None}

    def include_name(name, type_, parent_names):
        if type_ == "schema":
            return name in managed_schemas
        return True

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=translated,
            compare_type=True,
            transaction_per_migration=True,
            include_schemas=True,
            include_name=include_name,
        )

        with context.begin_transaction():
            context.run_migrations()



if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
