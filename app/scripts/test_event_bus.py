"""Test del bus de eventos (Redis Streams + reparto en memoria).

No hay pytest en el proyecto, así que es un script autoejecutable. Necesita el
Redis de `docker-compose.yml` levantado:

    docker compose up -d redis
    python -m app.scripts.test_event_bus

Cubre los puntos de verificación del diseño:
  · aislamiento — el comensal de una mesa no ve nada de otra mesa ni de otro tenant;
  · filtrado por canal;
  · backpressure — cliente que no consume se marca para `resync`;
  · replay y detección de hueco (`MAXLEN` pequeño);
  · ciclo de vida del lector (arranque perezoso, parada con margen de gracia).

Usa tenant_ids negativos para no pisar streams reales, y limpia al terminar.
"""
import asyncio

from app.core import events
from app.core.config import settings
from app.core.event_bus import EventBus, id_le
from app.core.events import CH_STAFF, session_channel, stream_key, version_of

# Fuera del rango de tenants reales (los ids de shared.tenants son positivos).
T1, T2 = -9001, -9002
MESA_A, MESA_B = "aaaa-1111", "bbbb-2222"


async def _drain(sub, *, timeout=1.5):
    """Todo lo que haya en la cola dentro del timeout."""
    out = []
    while True:
        try:
            out.append(await asyncio.wait_for(sub.queue.get(), timeout=timeout))
        except asyncio.TimeoutError:
            return out


def _publish(tenant_id, tipo, canales, **payload):
    return events.publish(tenant_id, type=tipo, channels=canales, payload=payload)


async def test_version_monotona():
    """`v` debe crecer siempre, y comparar ids no puede ser comparar strings."""
    assert version_of("1730-0") < version_of("1730-1") < version_of("1731-0")
    # El bug clásico: '9-0' > '10-0' como texto.
    assert id_le("9-0", "10-0")
    assert not id_le("10-0", "9-0")
    assert id_le("5-1", "5-1")
    print("  ok  · version_of monótona e id_le compara numéricamente")


async def test_block_sobrevive_al_socket_timeout(bus: EventBus):
    """El `XREAD BLOCK` debe aguantar el bloqueo entero sin reventar.

    redis-py 8 corta toda lectura a los 5 s **aunque se le pase
    `socket_timeout=None`**. Sin un `socket_timeout` explícito mayor que el
    BLOCK, el lector explota cada 5 s, escupe un traceback y reintenta para
    siempre: funciona de milagro y llena el log. Es un fallo silencioso, de ahí
    el test.
    """
    import time
    key = stream_key(-9998)
    t0 = time.monotonic()
    resp = await bus._redis.xread({key: "$"}, block=6000, count=10)
    dt = time.monotonic() - t0

    assert resp in ([], None), f"el stream de prueba debía estar vacío: {resp!r}"
    assert dt >= 5.5, f"el BLOCK de 6 s se cortó a los {dt:.2f} s (¿socket_timeout?)"
    print(f"  ok  · XREAD BLOCK aguanta los 6 s completos ({dt:.2f} s)")


async def test_fanout_y_aislamiento(bus: EventBus):
    """El corazón de la seguridad: los canales no se filtran entre sí."""
    comensal_a = await bus.subscribe(T1, frozenset({session_channel(MESA_A)}))
    comensal_b = await bus.subscribe(T1, frozenset({session_channel(MESA_B)}))
    staff = await bus.subscribe(T1, frozenset({CH_STAFF}))
    otro_tenant = await bus.subscribe(T2, frozenset({CH_STAFF, session_channel(MESA_A)}))

    await asyncio.sleep(0.3)  # que arranquen los lectores

    _publish(T1, "order.created", [CH_STAFF, session_channel(MESA_A)], order_id="o1")
    _publish(T1, "order.item_kitchen_changed", [CH_STAFF, session_channel(MESA_B)], item_id="i1")
    _publish(T1, "table.status_changed", [CH_STAFF], status="libre")

    a, b, s, otro = (
        await _drain(comensal_a), await _drain(comensal_b),
        await _drain(staff), await _drain(otro_tenant, timeout=0.5),
    )

    assert [e.type for e in a] == ["order.created"], [e.type for e in a]
    assert [e.type for e in b] == ["order.item_kitchen_changed"], [e.type for e in b]
    assert len(s) == 3, [e.type for e in s]
    assert otro == [], "¡fuga entre tenants!"
    print("  ok  · cada comensal solo ve su mesa; staff ve los 3; el otro tenant, nada")

    # El payload llega íntegro.
    assert a[0].data["order_id"] == "o1", a[0].data
    assert a[0].v > 0 and a[0].at
    print("  ok  · payload, `v` y `at` llegan íntegros")

    for sub in (comensal_a, comensal_b, staff, otro_tenant):
        await bus.unsubscribe(sub)


async def test_backpressure(bus: EventBus):
    """Un cliente que no consume se descarta, no se le acumula memoria."""
    original = settings.REALTIME_QUEUE_SIZE
    settings.REALTIME_QUEUE_SIZE = 3
    try:
        lento = await bus.subscribe(T1, frozenset({CH_STAFF}))
        await asyncio.sleep(0.3)

        for i in range(10):
            _publish(T1, "table.status_changed", [CH_STAFF], n=i)
        await asyncio.sleep(0.8)

        assert lento.dropped, "la cola se llenó y no se marcó para resync"
        assert lento.queue.qsize() <= 3, lento.queue.qsize()
        print(f"  ok  · cola acotada a 3 y marcada para resync ({lento.queue.qsize()} en cola)")
        await bus.unsubscribe(lento)
    finally:
        settings.REALTIME_QUEUE_SIZE = original


async def test_replay(bus: EventBus):
    """Reconectar con Last-Event-ID recupera lo perdido, en orden y sin huecos."""
    primero = _publish(T1, "order.created", [CH_STAFF], n=0)
    ids = [_publish(T1, "order.confirmed", [CH_STAFF], n=i) for i in range(1, 6)]

    perdidos, hueco = await bus.history(T1, primero, limit=settings.REALTIME_REPLAY_MAX)
    assert not hueco, "no debería haber hueco: todo sigue en el stream"
    assert [e.id for e in perdidos] == ids, [e.id for e in perdidos]
    assert [e.data["n"] for e in perdidos] == [1, 2, 3, 4, 5]
    print(f"  ok  · replay recupera los {len(perdidos)} eventos perdidos, en orden")

    # Rango exclusivo: pedir "después del último" no devuelve el último otra vez.
    vacio, hueco = await bus.history(T1, ids[-1], limit=100)
    assert vacio == [] and not hueco
    print("  ok  · el rango es exclusivo (no se repite el último evento)")


async def test_hueco(bus: EventBus):
    """Desconectado más allá de la retención → `resync`, no un vacío silencioso."""
    viejo = _publish(T2, "order.created", [CH_STAFF], n=0)
    for i in range(1, 8):
        _publish(T2, "order.confirmed", [CH_STAFF], n=i)

    # El recorte de producción es `MAXLEN ~` (aproximado): Redis solo poda en los
    # límites de nodo, así que con 8 entradas puede no podar nada. Para provocar
    # el hueco de verdad hace falta un XTRIM exacto.
    await bus._redis.xtrim(stream_key(T2), maxlen=2, approximate=False)

    _, hueco = await bus.history(T2, viejo, limit=settings.REALTIME_REPLAY_MAX)
    assert hueco, "el evento pedido ya no está en el stream: debía reportar hueco"
    print("  ok  · id caído por MAXLEN → hueco (el cliente recargará por REST)")

    # Y un stream que nunca existió NO es un hueco: no se perdió nada.
    _, hueco_vacio = await bus.history(-9999, "1-0", limit=100)
    assert not hueco_vacio, "un stream inexistente no debe forzar resync"
    print("  ok  · stream inexistente no es hueco")


async def test_ciclo_de_vida_del_lector(bus: EventBus):
    """Un lector por tenant, perezoso, y con margen de gracia al soltarlo.

    El margen se baja a 1 s al arrancar la corrida (ver `main`), porque los
    lectores de los tests anteriores lo capturan al programarse.
    """
    # Los tests anteriores dejaron lectores en su margen de gracia.
    await asyncio.sleep(1.5)
    assert not bus._readers, f"quedaron lectores sin parar: {list(bus._readers)}"
    print("  ok  · los lectores de los tests anteriores se pararon solos")

    s1 = await bus.subscribe(T1, frozenset({CH_STAFF}))
    s2 = await bus.subscribe(T1, frozenset({session_channel(MESA_A)}))
    assert len(bus._readers) == 1, bus._readers
    print("  ok  · 2 suscriptores del mismo tenant comparten UN lector")

    await bus.unsubscribe(s1)
    assert T1 in bus._readers, "con un suscriptor vivo el lector sigue"

    await bus.unsubscribe(s2)
    assert T1 in bus._readers, "no debe pararse en el acto (margen de gracia)"

    # Reconexión dentro del margen: el lector se conserva.
    s3 = await bus.subscribe(T1, frozenset({CH_STAFF}))
    await asyncio.sleep(1.3)
    assert T1 in bus._readers, "reconectar dentro del margen debe conservar el lector"
    print("  ok  · reconectar dentro del margen no recrea el lector (F5 del cajero)")

    await bus.unsubscribe(s3)
    await asyncio.sleep(1.5)
    assert T1 not in bus._readers, "sin suscriptores, el lector debe pararse"
    print("  ok  · sin suscriptores el lector se para tras el margen")


async def _limpiar():
    from redis.asyncio import Redis
    r = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    await r.delete(stream_key(T1), stream_key(T2))
    await r.aclose()


async def main():
    print("Bus de eventos (Redis Streams)")
    # Antes de crear nada: el margen de gracia se captura al programar la parada,
    # así que bajarlo a mitad de corrida no afecta a los lectores ya soltados.
    settings.REALTIME_READER_LINGER_SECONDS = 1
    await _limpiar()
    await test_version_monotona()

    bus = EventBus()
    await bus.start()
    try:
        await test_block_sobrevive_al_socket_timeout(bus)
        await test_fanout_y_aislamiento(bus)
        await test_backpressure(bus)
        await test_replay(bus)
        await test_hueco(bus)
        await test_ciclo_de_vida_del_lector(bus)
    finally:
        await bus.aclose()
        await _limpiar()
    print("TODO OK ✔")


if __name__ == "__main__":
    asyncio.run(main())
