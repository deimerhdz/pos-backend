"""Hub de suscripciones en memoria, alimentado por un lector de Redis por tenant.

La pieza que hace esto escalable: **un lector por proceso y tenant, no por
conexión**. Cada proceso mantiene *una* conexión Redis con `XREAD BLOCK` sobre el
stream del tenant y reparte en memoria a sus suscriptores locales por
`asyncio.Queue`. Con 20.000 conexiones repartidas en 8 procesos, Redis ve 8
lectores, no 20.000.

Consecuencia de diseño que conviene subrayar: **no hacen falta sticky sessions**.
El estado de suscripción se deriva del token en cada conexión y los eventos vienen
de Redis, así que cualquier instancia puede atender a cualquier cliente y el
balanceador puede repartir a ciegas.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings
from app.core.events import stream_key, version_of

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Event:
    """Un evento ya listo para serializar a SSE."""
    id: str
    type: str
    channels: frozenset[str]
    at: str
    data: dict[str, Any]
    v: int


def parse_entry(entry_id: str, fields: dict[str, str]) -> Event:
    """Convierte una entrada cruda de Redis en un `Event`."""
    raw = fields.get("d") or "{}"
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("Evento %s con payload ilegible; se entrega vacío", entry_id)
        data = {}
    return Event(
        id=entry_id,
        type=fields.get("type", "unknown"),
        channels=frozenset(c for c in (fields.get("ch") or "").split(",") if c),
        at=fields.get("at", ""),
        data=data,
        v=version_of(entry_id),
    )


def id_le(a: str, b: str) -> bool:
    """¿`a` <= `b` como ids de stream? Comparar strings no vale: '9-0' > '10-0'."""
    def parts(x: str) -> tuple[int, int]:
        ms, _, seq = x.partition("-")
        return int(ms), int(seq or 0)
    return parts(a) <= parts(b)


# `eq=False` para conservar el hash por identidad: las suscripciones viven en un
# `set` y dos clientes distintos con los mismos canales no son el mismo suscriptor.
@dataclass(slots=True, eq=False)
class Subscription:
    tenant_id: int
    channels: frozenset[str]
    queue: asyncio.Queue = field(repr=False)
    #: Se llenó la cola: el cliente perdió eventos y hay que mandarlo a `resync`
    #: en vez de acumular memoria por él.
    dropped: bool = False

    def wants(self, event: Event) -> bool:
        return not self.channels.isdisjoint(event.channels)


class EventBus:
    """Lectores de Redis por tenant + reparto en memoria a los suscriptores."""

    def __init__(self) -> None:
        self._redis: Redis | None = None
        self._subs: dict[int, set[Subscription]] = {}
        self._readers: dict[int, asyncio.Task] = {}
        #: Señal de parada por tenant. Hace falta **además** de `task.cancel()`:
        #: redis-py envuelve el `CancelledError` que interrumpe un `XREAD BLOCK`
        #: en su propio `redis.exceptions.TimeoutError`, así que el `except` que
        #: reintenta ante fallos de red se tragaría la cancelación y el lector
        #: seguiría vivo para siempre.
        self._stops: dict[int, asyncio.Event] = {}
        self._linger: dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    # ------------------------------------------------------------- ciclo de vida
    async def start(self) -> None:
        """Abre la conexión propia y comprueba que Redis responde.

        Conexión **propia**, no la de `token_blocklist`: un `XREAD BLOCK`
        monopoliza una conexión del pool durante todo el bloqueo, y ese pool sirve
        también al blocklist de JWT y al rate limit en cada request.
        """
        self._closed = False
        # `socket_timeout` explícito y **mayor que el BLOCK**: redis-py 8 corta
        # toda lectura a los 5 s aunque se le pase `socket_timeout=None`, así que
        # sin esto un `XREAD BLOCK 15000` revienta a los 5 s, cada 5 s, para
        # siempre. El margen absorbe la latencia de red por encima del bloqueo.
        self._redis = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=settings.REALTIME_READER_BLOCK_MS / 1000 + 10,
        )
        await self._redis.ping()

    async def aclose(self) -> None:
        self._closed = True
        async with self._lock:
            for stop in self._stops.values():
                stop.set()
            tasks = list(self._readers.values()) + list(self._linger.values())
            self._readers.clear()
            self._stops.clear()
            self._linger.clear()
            self._subs.clear()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    # ------------------------------------------------------------- suscripciones
    async def subscribe(self, tenant_id: int, channels: frozenset[str]) -> Subscription:
        sub = Subscription(
            tenant_id=tenant_id,
            channels=channels,
            queue=asyncio.Queue(maxsize=settings.REALTIME_QUEUE_SIZE),
        )
        async with self._lock:
            self._subs.setdefault(tenant_id, set()).add(sub)
            # Si el tenant estaba en el margen de gracia, se cancela la parada.
            linger = self._linger.pop(tenant_id, None)
            if linger is not None:
                linger.cancel()
            if tenant_id not in self._readers:
                stop = asyncio.Event()
                self._stops[tenant_id] = stop
                self._readers[tenant_id] = asyncio.create_task(
                    self._read_loop(tenant_id, stop), name=f"rt-reader-{tenant_id}"
                )
        return sub

    async def unsubscribe(self, sub: Subscription) -> None:
        async with self._lock:
            peers = self._subs.get(sub.tenant_id)
            if peers is None:
                return
            peers.discard(sub)
            if peers or self._closed:
                return
            del self._subs[sub.tenant_id]
            # Margen de gracia antes de parar el lector: sin esto, un F5 del
            # cajero destruye y recrea la task y la conexión Redis.
            self._linger[sub.tenant_id] = asyncio.create_task(
                self._stop_after_linger(sub.tenant_id), name=f"rt-linger-{sub.tenant_id}"
            )

    async def _stop_after_linger(self, tenant_id: int) -> None:
        try:
            await asyncio.sleep(settings.REALTIME_READER_LINGER_SECONDS)
        except asyncio.CancelledError:
            return
        async with self._lock:
            self._linger.pop(tenant_id, None)
            if self._subs.get(tenant_id):
                return  # volvió alguien justo al final
            reader = self._readers.pop(tenant_id, None)
            stop = self._stops.pop(tenant_id, None)
        if stop is not None:
            stop.set()  # que el `except` no confunda la cancelación con un fallo
        if reader is not None:
            reader.cancel()

    # -------------------------------------------------------------------- lector
    async def _read_loop(self, tenant_id: int, stop: asyncio.Event) -> None:
        """Un `XREAD BLOCK` por tenant, repartiendo a los suscriptores locales.

        Arranca desde `$` (solo lo nuevo): recuperar lo perdido es cosa de cada
        conexión con `history()`, no del lector, que es compartido.
        """
        assert self._redis is not None
        key = stream_key(tenant_id)
        last_id = "$"
        while not self._closed and not stop.is_set():
            try:
                resp = await self._redis.xread(
                    {key: last_id}, block=settings.REALTIME_READER_BLOCK_MS, count=200
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Ojo: redis-py entrega el CancelledError que interrumpe el BLOCK
                # como un `TimeoutError` suyo, así que sin esta comprobación una
                # parada se confundiría con un fallo de red y el lector seguiría
                # vivo indefinidamente.
                if self._closed or stop.is_set():
                    return
                logger.warning("XREAD falló para el tenant %s; reintentando", tenant_id, exc_info=True)
                await asyncio.sleep(1)
                continue

            if not resp:
                continue  # timeout del BLOCK, sin novedades

            for _key, entries in resp:
                for entry_id, fields in entries:
                    last_id = entry_id
                    self._fanout(tenant_id, parse_entry(entry_id, fields))

    def _fanout(self, tenant_id: int, event: Event) -> None:
        for sub in self._subs.get(tenant_id, ()):
            if sub.dropped or not sub.wants(event):
                continue
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Cliente que no consume: se le marca y el generador lo cierra
                # con `resync`. Acumular por él es cómo se agota la memoria.
                sub.dropped = True
                logger.info(
                    "Suscriptor del tenant %s no consume; se le enviará resync", tenant_id
                )

    # ------------------------------------------------------------------- replay
    async def history(
        self, tenant_id: int, after_id: str, limit: int
    ) -> tuple[list[Event], bool]:
        """Eventos posteriores a `after_id`, y si se detectó un hueco.

        Un hueco significa que el cliente estuvo desconectado más allá de la
        retención (`MAXLEN`): se le manda `resync` para que recargue por REST, que
        es una salida honesta y explícita en vez de un vacío silencioso.
        """
        if self._redis is None:
            return [], False
        key = stream_key(tenant_id)
        try:
            # Rango exclusivo por la izquierda: `(id` (Redis >= 6.2).
            raw = await self._redis.xrange(key, f"({after_id}", "+", count=limit + 1)
            oldest = await self._redis.xrange(key, "-", "+", count=1)
        except Exception:
            logger.warning("XRANGE falló para el tenant %s", tenant_id, exc_info=True)
            return [], True  # ante la duda, resync: mejor recargar de más

        # Stream vacío: no se perdió nada porque nunca se publicó nada.
        if not oldest:
            return [], False

        # Lo que el cliente pidió ya se cayó por MAXLEN.
        if not id_le(oldest[0][0], after_id):
            return [], True

        # Más eventos que el tope: recargar por REST sale más barato que repetirlos.
        if len(raw) > limit:
            return [], True

        return [parse_entry(eid, fields) for eid, fields in raw], False


#: Instancia de proceso. `main.py` la arranca en el lifespan.
event_bus = EventBus()
