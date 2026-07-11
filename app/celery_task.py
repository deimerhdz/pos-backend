from celery import Celery

from app.core.mail import send_email as send_email_resend, create_message

c_app = Celery("pos")

c_app.config_from_object("app.core.config")


@c_app.task
def send_email_task(recipients: list[str], subject: str, body: str):
    message = create_message(recipients=recipients, subject=subject, body=body)
    send_email_resend(message)
    print("Email sent")


@c_app.task
def release_expired_reservations_task() -> int:
    """Libera reservas de inventario vencidas en todos los tenants (Fase 2).

    Pensada para correr periódicamente (celery beat). Itera los schemas de tenant
    y libera reservas activas expiradas de órdenes aún pendientes.
    """
    from sqlalchemy import select

    from app.core.db import with_db
    from app.core.models import Tenant
    from app.api.v1.orders.reservations import release_expired

    with with_db(None) as db:
        schemas = [t.schema for t in db.execute(select(Tenant)).scalars()]

    total = 0
    for schema in schemas:
        with with_db(schema) as db:
            total += release_expired(db)
            db.commit()
    return total
