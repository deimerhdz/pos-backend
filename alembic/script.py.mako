"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}

@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """Upgrade schema."""
    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    schema_quoted = preparer.format_schema(schema)
    <%
        import re
        _upgrade = re.sub(r"schema=['\"]tenant_default['\"]", "schema=schema", upgrades if upgrades else "pass")
    %>
    ${_upgrade}


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Downgrade schema."""
    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    schema_quoted = preparer.format_schema(schema)
    <%
        import re
        _downgrade = re.sub(r"schema=['\"]tenant_default['\"]", "schema=schema", downgrades if downgrades else "pass")
    %>
    ${_downgrade}
