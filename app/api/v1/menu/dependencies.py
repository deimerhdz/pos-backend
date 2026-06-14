"""Dependencia de sesión de menú: valida el token público de comensal."""
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.table_session import TableSession


def get_menu_session(
    x_menu_session: str = Header(
        ..., alias="X-Menu-Session", description="Token de la sesión de menú."
    ),
    db: Session = Depends(get_db),
) -> TableSession:
    """Resuelve y valida la sesión de menú a partir del header 'X-Menu-Session'."""
    session = db.execute(
        select(TableSession).where(
            TableSession.token == x_menu_session,
            TableSession.active == True,  # noqa: E712
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión de menú inválida o cerrada",
        )

    return session
