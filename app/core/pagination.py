from math import ceil
from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int


def paginate(db: Session, stmt: Select, page: int, size: int) -> dict:
    """Ejecuta `stmt` paginado y devuelve el dict para construir un `Page`.

    Calcula el total envolviendo la consulta (respeta filtros/joins) y aplica
    offset/limit sobre la misma sentencia.
    """
    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()

    items = db.execute(stmt.offset((page - 1) * size).limit(size)).scalars().all()

    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "pages": ceil(total / size) if total else 0,
    }
