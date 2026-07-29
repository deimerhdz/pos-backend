"""Barrido de sesiones de mesa huérfanas.

Una mesa se marca `ocupada` cuando el primer comensal escanea el QR. La expiración
del comensal es **perezosa** —solo se evalúa cuando vuelve a llamar con su token—
así que quien cierra la pestaña no expira nunca y la mesa se quedaría ocupada
indefinidamente, sin poder recibir clientes. Este job es la red que lo evita.

Cierra por **dos criterios distintos**, no por un umbral con dos valores:

- **Abandonada sin pedir** (`EMPTY_SESSION_TTL_MINUTES`, 30 min): alguien escaneó
  y se fue sin pedir nada. No hay nada que perder, así que se suelta pronto.
- **Olvidada con consumo** (`TABLE_SESSION_MAX_HOURS`, 6 h): tope duro. Aquí sí
  puede haber pedidos, y bajar el umbral echaría a gente que sigue comiendo.

La distinción importa: el riesgo de soltar demasiado pronto una mesa **con** pedidos
es real (se cierra la sesión de alguien que está esperando su comida); el de soltar
una mesa que nadie usó, ninguno.

El camino normal no depende de este job: el comensal que pulsa "Salir" libera la
mesa en el acto (`POST /cart/leave`). Esto cubre a quien no pulsa nada.

Dos detalles que no son opcionales:

- **Itera todos los schemas de tenant.** El job no vive dentro de un request, así
  que no hay `x-tenant-host` ni token del que sacar el tenant: hay que recorrer
  `shared.tenants` y abrir una sesión por cada uno.
- **Toma un lock en Redis.** Con varios workers de uvicorn el scheduler arranca en
  cada proceso; sin el lock el barrido correría N veces en paralelo sobre las
  mismas filas.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.core.config import settings
from app.core.db import with_db
from app.core.redis import token_blocklist as redis

logger = logging.getLogger(__name__)

_LOCK_KEY = "lock:sweep_table_sessions"


def _abandonadas_sin_pedir(db, now: datetime) -> list:
    """Sesiones que hay que soltar ya: **sin ningún pedido** y sin nadie activo
    desde hace más de `EMPTY_SESSION_TTL_MINUTES`.

    Es el caso de "alguien escaneó por curiosidad y se fue". No espera al tope de
    6 h porque no hay nada que perder: no se pidió nada.

    El detalle que hace que esto funcione: `expires_at` **no** es el instante de la
    última actividad, sino ése más `SESSION_TTL_MINUTES` (la ventana deslizante de
    4 h). Compararlo contra `now` a secas no dispararía nunca dentro de esas 4 h,
    que es justo el agujero que se quiere cerrar. Hay que revertir el desplazamiento
    para recuperar la última actividad real.
    """
    from app.models.session_participant import SessionParticipant
    from app.models.table_session import TableSession
    from app.api.v1.table_sessions.service import has_billable_orders

    limite = now - timedelta(minutes=settings.EMPTY_SESSION_TTL_MINUTES)
    # `expires_at - SESSION_TTL_MINUTES` = última actividad del comensal.
    ultima_actividad = SessionParticipant.expires_at - timedelta(
        minutes=settings.SESSION_TTL_MINUTES
    )

    candidatas = db.execute(
        select(TableSession).where(
            TableSession.status == "active",
            # Ningún comensal sigue dentro de su ventana de inactividad.
            ~select(SessionParticipant.id)
            .where(
                SessionParticipant.table_session_id == TableSession.id,
                SessionParticipant.status == "open",
                SessionParticipant.expires_at.is_not(None),
                ultima_actividad > limite,
            )
            .exists(),
        )
    ).scalars().all()

    return [ts for ts in candidatas if not has_billable_orders(db, ts.id)]


def _sweep_schema(schema: str, corte: datetime) -> int:
    """Cierra las sesiones vencidas de un tenant. Devuelve cuántas cerró."""
    from app.models.customer_order import CustomerOrder
    from app.models.dining_table import DiningTable
    from app.models.table_session import TableSession
    from app.api.v1.orders.checkout import TERMINAL, close_table_sessions

    cerradas = 0
    with with_db(schema) as db:
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Dos criterios distintos, no uno con dos umbrales:
        #  - mesas abandonadas sin pedir → ventana corta (30 min);
        #  - sesiones olvidadas con consumo → tope duro de 6 h.
        vencidas = list(_abandonadas_sin_pedir(db, now))
        ids = {ts.id for ts in vencidas}

        vencidas += [
            ts for ts in db.execute(
                select(TableSession).where(
                    TableSession.status == "active",
                    TableSession.opened_at < corte,
                )
            ).scalars().all()
            if ts.id not in ids
        ]

        for ts in vencidas:
            table_id = ts.dining_table_id
            # `closed_by=None`: no la cerró un humano, la cerró el barrido.
            close_table_sessions(db, table_id, closed_by=None)

            # La mesa solo vuelve a 'libre' si no queda nada sin cobrar; si hay
            # pedidos vivos es un problema real que debe ver el staff, no algo
            # que el job deba tapar.
            pendientes = db.execute(
                select(CustomerOrder.id).where(
                    CustomerOrder.dining_table_id == table_id,
                    CustomerOrder.status.notin_(TERMINAL),
                ).limit(1)
            ).scalar()
            table = db.get(DiningTable, table_id)
            if table is not None and pendientes is None:
                table.status = "libre"
            elif pendientes is not None:
                logger.warning(
                    "Sesión huérfana %s (schema %s) cerrada, pero la mesa sigue "
                    "ocupada: tiene pedidos sin cobrar",
                    ts.id, schema,
                )
            cerradas += 1

        if cerradas:
            db.commit()
    return cerradas


def sweep_orphan_sessions() -> int:
    """Recorre todos los tenants cerrando sesiones de mesa vencidas."""
    corte = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=settings.TABLE_SESSION_MAX_HOURS
    )
    total = 0
    with with_db(None) as db:
        schemas = [r[0] for r in db.execute(
            text("SELECT schema FROM shared.tenants")
        ).fetchall()]

    for schema in schemas:
        try:
            total += _sweep_schema(schema, corte)
        except Exception:
            # Un tenant roto no debe impedir el barrido de los demás.
            logger.exception("Error barriendo sesiones del schema %s", schema)

    if total:
        logger.info("Barrido de sesiones: %d sesión(es) de mesa cerradas", total)
    return total


async def _run_with_lock() -> None:
    """Un solo worker ejecuta el barrido por ciclo. El TTL del lock es la mitad
    del intervalo: si el proceso muere a medias, el siguiente ciclo lo retoma."""
    ttl = max(int(settings.SESSION_SWEEP_INTERVAL_MINUTES * 60 / 2), 30)
    try:
        got = await redis.set(_LOCK_KEY, "1", ex=ttl, nx=True)
    except Exception:
        logger.warning("Redis no disponible; se omite el barrido de sesiones", exc_info=True)
        return
    if not got:
        return  # otro worker lo está haciendo

    import anyio
    await anyio.to_thread.run_sync(sweep_orphan_sessions)


def start_scheduler():
    """Arranca el barrido periódico. Devuelve el scheduler (o None si APScheduler
    no está instalado, para no tumbar la app por una dependencia opcional)."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        logger.warning(
            "APScheduler no está instalado: las sesiones de mesa huérfanas no se "
            "cerrarán solas. Instala 'apscheduler' o corre "
            "`python -m app.scripts.sweep_sessions` por cron."
        )
        return None

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_with_lock,
        IntervalTrigger(minutes=settings.SESSION_SWEEP_INTERVAL_MINUTES),
        id="sweep_orphan_table_sessions",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info(
        "Barrido de sesiones activo: cada %d min, cierra las de más de %d h",
        settings.SESSION_SWEEP_INTERVAL_MINUTES, settings.TABLE_SESSION_MAX_HOURS,
    )
    return scheduler
