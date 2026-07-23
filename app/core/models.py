import uuid
from sqlalchemy import Integer,String,Column,DateTime, UniqueConstraint,func,Boolean,MetaData,ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm  import mapped_column,Mapped,DeclarativeBase,relationship

from typing import Optional,List

convention = {
    'all_column_names': lambda constraint, table: '_'.join(
        [column.name for column in constraint.columns.values()]
    ),
    'ix': 'ix__%(table_name)s__%(all_column_names)s',
    'uq': 'uq__%(table_name)s__%(all_column_names)s',
    'ck': 'ck__%(table_name)s__%(constraint_name)s',
    'fk': 'fk__%(table_name)s__%(all_column_names)s__%(referred_table_name)s',
    'pk': 'pk__%(table_name)s',
}

class Base(DeclarativeBase):
     metadata = MetaData(schema="tenant",naming_convention=convention)

class TimestampMixin:
    created_at = Column(
        DateTime,
        server_default=func.now(),
        nullable=False,
        comment="Record creation time"
    )
    updated_at = Column(
        DateTime,
        onupdate=func.now(),
        nullable=True,
        comment="Unique record identifier"
    )
    
class UUIDPrimaryKeyMixin:
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Unique record identifier"
    )
class Tenant(Base,TimestampMixin):
    __tablename__ = "tenants"

    id:Mapped[int] = mapped_column("id",Integer, primary_key=True, nullable=False)
    
    name:Mapped[str] = mapped_column("name",String(255), nullable=False, index=True, unique=True)
    
    schema:Mapped[str] = mapped_column("schema", String(255), nullable=False, unique=True)
    
    plan:Mapped[str] = mapped_column("plan", String(100), nullable=False, default="basic")
    
    host:Mapped[str] = mapped_column("host", String(255), nullable=False, unique=True)

    logo_url:Mapped[Optional[str]] = mapped_column("logo_url", String(500), nullable=True)

    users: Mapped[list["User"]] = relationship(
            back_populates="tenant",
            cascade="all, delete-orphan"
        )
    
    __table_args__ = ({"schema": "shared"},)

class Role(UUIDPrimaryKeyMixin,TimestampMixin,Base):
    __tablename__ = "roles"    
    
    name:Mapped[str] = mapped_column(String(150),nullable=False)
    
    active:Mapped[bool] = mapped_column(Boolean, default=True)
    
    users:Mapped[List["User"]] = relationship(back_populates="role")
    
    __table_args__ = ({"schema": "shared"},) 


class User(UUIDPrimaryKeyMixin,TimestampMixin,Base):
    __tablename__ = "users"

    name:Mapped[str] = mapped_column(String(150),nullable=False)
    
    email:Mapped[str] = mapped_column(String(255),nullable=False,index=True)
    
    password_hash:Mapped[str] =  mapped_column(String(255),nullable=False)
    
    phone:Mapped[Optional[str]] =  mapped_column(String(20),nullable=True)
    
    active:Mapped[bool] = mapped_column(Boolean, default=True)

    must_change_password:Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    role_id: Mapped[UUID] = mapped_column(ForeignKey("shared.roles.id"))
    
    role:Mapped[Optional["Role"]] = relationship(back_populates="users")
    
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("shared.tenants.id"),
        nullable=True,
        index=True
    )

    tenant: Mapped["Tenant"] = relationship(
        back_populates="users"
    )

    __table_args__ = (UniqueConstraint(
        "tenant_id",
        "email",
        name="uq_user_tenant_email"
    ),{"schema": "shared"},)

    @property
    def role_name(self) -> Optional[str]:
        return self.role.name if self.role else None

    @property
    def tenant_name(self) -> Optional[str]:
        return self.tenant.name if self.tenant else None


