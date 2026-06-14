import functools
from typing import Callable

from typeguard import typechecked
from alembic import op
from sqlalchemy import text


@typechecked
def for_each_tenant_schema(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapped():
        bind = op.get_bind()
        bind.execute(text("CREATE SCHEMA IF NOT EXISTS tenant_default"))
        schemas = bind.execute(text("SELECT schema FROM shared.tenants")).fetchall()
        all_schemas = ["tenant_default"] + [s[0] for s in schemas]
        for schema in all_schemas:
            func(schema)

    return wrapped