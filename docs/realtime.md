# Tiempo real (SSE + Redis Streams)

Sustituye el sondeo de los dos clientes (menú QR, terminal de mesas) por
notificaciones push. La latencia baja de ~10 s a <100 ms y el trabajo por cambio
pasa de O(clientes × frecuencia) a O(destinatarios del evento) ≈ 4.

> ⚠️ **Antes de desplegar hay que tocar nginx.** Ver [Despliegue](#despliegue).
> Sin `proxy_buffering off` el proxy acumula la respuesta y el cliente no recibe
> nada: el stream parece colgado.

---

## Por qué SSE y no WebSockets

El flujo es unidireccional servidor→cliente: el comensal nunca envía nada por
este canal, sus pedidos van por `POST` REST. SSE da el 100 % del beneficio y trae
gratis lo que con WebSockets habría que escribir a mano —reconexión automática,
backoff y replay vía `Last-Event-ID`— sobre HTTP/1.1 plano, sin upgrade de
protocolo ni más superficie de proxy.

El día que aparezca tráfico cliente→servidor con estado (chat con el mesero,
comandas por voz) se migra: el diseño de canales y eventos no cambia, solo el
transporte.

## Por qué Redis Streams y no Pub/Sub

Los ids de un stream son **persistentes y ordenados**, así que el id del evento
SSE *es* el id de la entrada: el `Last-Event-ID` que el navegador reenvía solo se
traduce en un `XRANGE` exacto y el replay sale gratis. Con Pub/Sub habría que
construir ese buffer aparte. Y Redis ya estaba: no se añade infraestructura.

---

## Arquitectura

```
PATCH /orders/items/{id}/kitchen
   │
   ├─ UPDATE + COMMIT en Postgres          ← la fuente de verdad
   │
   └─ XADD events:tenant:{id}              ← DESPUÉS del commit, nunca dentro
          │
          ├─ XREAD BLOCK (instancia A) ──► asyncio.Queue ──► SSE ──► comensal
          └─ XREAD BLOCK (instancia B) ──► asyncio.Queue ──► SSE ──► cajero
```

- **Un stream por tenant** (`events:tenant:{id}`), filtrado por canal en memoria.
  Por mesa fragmentaría demasiado (`XREAD` sobre claves cambiantes) y el volumen
  por tenant es bajísimo.
- **Un lector por proceso y tenant, no por conexión.** Con 20.000 conexiones en 8
  procesos, Redis ve 8 lectores.
- **Publicar siempre después del `COMMIT`.** Si la transacción falla no puede
  haber salido un evento anunciando algo que no ocurrió.
- **Sin sticky sessions.** El estado de suscripción se deriva del token y los
  eventos vienen de Redis, así que cualquier instancia atiende a cualquier
  cliente y el balanceador puede repartir a ciegas.

### Canales

| Canal | Quién | Qué recibe |
|---|---|---|
| `session:{table_session_id}` | Comensal | Solo lo de **su** sesión de mesa |
| `staff` | Terminal de mesas | Todo el tenant |

La suscripción **se deriva del token, nunca se pide**: el `table_session_id` va
firmado en el JWT del comensal, así que es imposible escuchar otra mesa u otro
tenant.

---

## Endpoints

### `GET /api/v1/realtime/stream`

| Parámetro | Quién | Notas |
|---|---|---|
| `token` | Comensal | Token de sesión (el mismo de `x-session-token`) |
| `ticket` | Staff | De un solo uso, 30 s, se obtiene en `POST /realtime/ticket` |
| `last_event_id` | Ambos | Respaldo de la cabecera `Last-Event-ID` |

Uno de `token` o `ticket`, nunca los dos (400). Un 401 aquí, **antes** de abrir
el stream, hace que `EventSource` deje de reintentar — que es lo que queremos
para una sesión muerta.

El endpoint no declara `Depends(get_db)` ni `Depends(get_tenant)`: `EventSource`
no manda cabeceras, así que no hay `x-tenant-host`. El tenant sale siempre del
claim firmado.

### `POST /api/v1/realtime/ticket`

Bearer normal. El Bearer **no** puede ir en la query del stream: acabaría en los
logs de nginx, el historial del navegador y el `Referer`. El ticket vive 30 s y
se consume con `GETDEL` (atómico: no se puede reusar).

---

## Catálogo de eventos

Eventos **delgados**: llevan ids y lo que cambió, no el objeto completo. El
cliente decide si le basta el evento o necesita re-consultar por REST.

| Tipo | Canales | Se publica en |
|---|---|---|
| `order.created` | staff + sesión | `cart/router.py` tras `submit_cart` |
| `order.confirmed` | staff + sesión | `orders/router.py` `confirm_order` |
| `order.item_kitchen_changed` | staff + sesión | `orders/router.py` `kitchen_transition` |
| `order.item_voided` | staff + sesión | `orders/router.py` `void_item` |
| `order.cancelled` | staff + sesión | `cancel_order` y `cancel_my_order` |
| `session.bill_changed` | staff + sesión | Junto a confirm/void/cancel |
| `payment.completed` | staff | `close_session` (una por venta) y `pay_order` |
| `session.closed` | staff + sesión | `close_session` y el barrido |
| `table.status_changed` | staff | `set_table_status`, `release_table`, `move_order`, `open_session` |

Control, fuera del catálogo y sin persistir: `hello`, `: ping`, `resync`.

Todos llevan `v` (versión monótona) y `at`. **`v` sale del id de la entrada de
Redis**, no de `CustomerOrder.version`: esa columna solo se incrementa al
bloquear y confirmar, nunca en las transiciones de cocina, que son justo las que
hay que ordenar. Redis es el único punto de serialización del tenant, así que sus
ids son monótonos entre procesos y sin skew de reloj.

**`session.bill_changed` no lleva `total`** a propósito: el cliente no debe
recargar la cuenta al recibirlo (le borraría al cajero lo que teclea), solo
marcarla obsoleta.

---

## Configuración

| Variable | Defecto | Para qué |
|---|---|---|
| `REALTIME_ENABLED` | `true` | **Interruptor de pánico**: en `false` el publicador es un no-op y el endpoint responde 503, así los clientes caen al sondeo sin desplegar frontend |
| `REALTIME_STREAM_MAXLEN` | `1000` | Retención por tenant. Más allá, `resync` |
| `REALTIME_HEARTBEAT_SECONDS` | `20` | `: ping`. **Debe ser < `proxy_read_timeout`** |
| `REALTIME_MAX_CONNECTION_SECONDS` | `1800` | Vida máxima; al vencer se revalida el token |
| `REALTIME_RETRY_MS` | `3000` | Backoff que se le indica al navegador |
| `REALTIME_QUEUE_SIZE` | `100` | Cola por cliente; si se llena, `resync` |
| `REALTIME_REPLAY_MAX` | `200` | Tope de replay; más allá, `resync` |
| `REALTIME_READER_BLOCK_MS` | `15000` | Bloqueo de cada `XREAD` |
| `REALTIME_READER_LINGER_SECONDS` | `30` | Margen antes de parar un lector sin suscriptores |
| `REALTIME_TICKET_TTL_SECONDS` | `30` | Vida del ticket del staff |
| `REALTIME_MAX_CONN_PER_SESSION` | `8` | Conexiones por mesa |
| `REALTIME_PUBLISH_TIMEOUT_SECONDS` | `0.25` | Timeout del `XADD` |

Relacionados (Fase 0):

| Variable | Defecto | Para qué |
|---|---|---|
| `SQL_ECHO` | `None` | `None` = deriva del entorno (dev sí, prod no) |
| `SESSION_TTL_REFRESH_SLACK_MINUTES` | `10` | Holgura del refresco deslizante. **Debe ser < `EMPTY_SESSION_TTL_MINUTES`** o el barrido cierra mesas activas |

---

## Despliegue

### nginx — obligatorio

La config de nginx vive en el VPS, no en este repo. Antes de desplegar hay que
añadir un bloque para la ruta del stream:

```nginx
location /api/v1/realtime/stream {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;

    # Sin esto nginx acumula la respuesta y el cliente no recibe NADA.
    proxy_buffering off;
    proxy_cache off;

    # Mayor que REALTIME_HEARTBEAT_SECONDS (20 s), o el proxy corta el túnel
    # en el silencio entre pings. Mayor también que la vida máxima de conexión
    # (1800 s) para que el corte lo decida la app, no el proxy.
    proxy_read_timeout 1900s;
    proxy_send_timeout 1900s;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header Connection '';
}
```

La app envía `X-Accel-Buffering: no` como cinturón, pero es un cinturón: solo
funciona si nginx lo respeta y no hay otro proxy delante.

### Checklist

1. [ ] Bloque de nginx aplicado y `nginx -t` en verde.
2. [ ] `nginx -s reload`.
3. [ ] Verificar el heartbeat a través del proxy:
   ```bash
   curl -N "https://api.skeilopos.com/api/v1/realtime/stream?token=<token>"
   # Debe imprimir `retry:`, `event: hello` y un `: ping` cada 20 s,
   # y sobrevivir más de un minuto.
   ```
4. [ ] Desplegar el backend y comprobar `✅ Bus de eventos listo` en el log.
5. [ ] Desplegar el frontend.
6. [ ] Si algo va mal: `REALTIME_ENABLED=false` + reinicio. Los clientes vuelven
   al sondeo de 10 s sin necesidad de desplegar frontend.

### Escalado

Con `--workers N` cada proceso mantiene su propio lector por tenant y el bus hace
su trabajo real entre procesos. No hacen falta sticky sessions. Por encima de
~5.000 conexiones por proceso conviene subir `ulimit -n` y el pool de Postgres.

---

## Tests

```bash
# Solo Redis, sin servidor
python -m app.scripts.test_event_bus
python -m app.scripts.test_session_ttl

# Con un servidor levantado
uvicorn app.main:app --port 8099 &
python -m app.scripts.test_realtime_stream
python -m app.scripts.e2e_qr_flow
```

`test_realtime_stream.py` cubre handshake, evento en vivo, aislamiento entre
mesas, replay con `Last-Event-ID`, ticket de un solo uso, 400/401 del handshake,
tope de conexiones por mesa y heartbeat.

> **Nota:** el fixture de `e2e_qr_flow` usa un prefijo de factura fijo (`E2E`),
> así que choca con las facturas que deje cualquier corrida cuyo teardown falle.
> Si aparece un `UniqueViolation` sobre `uq__invoices__prefix__number`, hay que
> limpiar las facturas `E2E` residuales.

---

## Trampas del cliente

Tres bugs reales del frontend que cualquier diseño push tiene que respetar; están
documentados en el código y cubiertos por tests:

1. **La campana** (`pos-terminal.store.ts`) deduplica por id, no por contador.
   Los eventos nunca la tocan: disparan una recarga y es `announcePending()`
   quien decide. Si no, el replay tras reconectar sonaría por pedidos ya vistos.

2. **La cuenta de la mesa** (`session-bill-panel.component.ts`) reinicia el pago
   cuando cambia. Por eso `session.bill_changed` solo la marca obsoleta y ofrece
   un botón "Actualizar": recargarla sola le borraría al cajero lo que teclea.

3. **`rt_v` protege la escritura optimista.** Un cliente que parchee el ítem en
   local puede descartar los eventos con `v` menor o igual a la ya aplicada;
   la versión se siembra con el `rt_v` que devuelve el propio PATCH.
