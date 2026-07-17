# Especificación: Gestión de Mesas con QR, Sesiones Anónimas y Facturación

## Contexto del sistema

Backend FastAPI multi-tenant (schema-based en PostgreSQL, `schema_translate_map`) para POS de heladería. Ya existe: inventario con consumo FEFO, catálogo de productos con recetas obligatorias, gestión de mesas/órdenes/caja. Esta especificación cubre el flujo QR → menú público → carrito anónimo por comensal → consolidación en mesero/caja → facturación.

## Decisiones de producto ya tomadas (no reabrir sin justificación nueva)

1. **Carrito por comensal, no por mesa.** Cada persona que escanea el QR tiene su propio carrito aislado, identificado por nombre (sin autenticación de negocio).
2. **Modelo de cobro: consolidación (opción C).** Los carritos individuales se agrupan en una orden de mesa al momento de cerrar, pero cada línea conserva trazabilidad de qué comensal la pidió, habilitando split de cuenta por comensal en caja.
3. **Quién consolida:** el mesero/caja dispara la consolidación manualmente ("cerrar pedido de mesa"). No es automática ni por timeout.
4. **Adiciones post-consolidación permitidas:** un comensal puede seguir agregando ítems a una orden ya consolidada, mientras esa orden no esté bloqueada para cobro.
5. **Múltiples órdenes por mesa a lo largo del ciclo de vida de la mesa** (no simultáneamente abiertas): solo una `order` en estado `abierta` por mesa a la vez; pueden coexistir con otras en `bloqueada`/`pagada`/`cancelada` de la misma mesa.
6. **Órdenes-hijas automáticas:** si llega una inserción de ítem y no hay `order` en estado `abierta` para esa mesa (porque la única existente está `bloqueada` en cobro), el sistema crea automáticamente una nueva `order` en estado `abierta` para la mesa. No hay error visible al comensal, no hay cola de reintentos.
7. **Liberación de mesa bloqueada por regla dura:** una mesa no puede pasar a `libre` mientras exista alguna `order` propia en estado distinto de `pagada`/`cancelada`. Esta regla vive en la capa de servicio/transacción, no solo en la UI.
8. **Cancelación de orden exige reversa de inventario**, no solo cambio de estado — mismo camino transaccional del descuento original, en reversa, con auditoría (quién, cuándo, motivo).
9. **Ciclo de pago y ciclo de cocina son estados independientes.** `order.estado` (pago) no describe si un ítem se está cocinando. Existe pantalla de cocina (KDS), por lo que cada `order_item` tiene su propio estado de cumplimiento, controlado por API en tiempo real, no por comanda manual.
10. **Editar un ítem ya en preparación o listo nunca es un `UPDATE` silencioso.** Se anula (`void`) el ítem original con motivo y se crea uno nuevo. Un ítem ya entregado no se "edita" — es reclamo/reproceso, flujo distinto fuera de esta especificación.
11. **La UI de caja ya soporta múltiples órdenes activas por mesa** — confirmado, no es research pendiente. La vista de mesa 5 puede mostrar N órdenes en distintos estados sin rediseño previo.
12. **Caja no puede bloquear una orden para cobro mientras tenga ítems en `pendiente` o `en_preparacion`.** El cobro solo se habilita cuando todo lo no anulado está `listo` o `entregado`.
13. **Camino de resolución cuando el comensal quiere pagar y cocina va tarde: cancelación parcial, no override de bloqueo.** El mesero anula (`void`) los `order_items` que aún no están listos usando el mismo mecanismo de fase 6 (reversa de inventario si aplica), y el bloqueo para cobro se habilita normalmente porque ya no quedan ítems pendientes. No existe un mecanismo separado de "forzar cobro con ítems en cocina" en esta versión — queda fuera de alcance v1.

## Modelo de datos (mínimo viable, dentro del esquema por tenant)

```
tables
  id, tenant_schema, number, estado (libre | ocupada)

table_sessions        -- 1 por comensal que escanea el QR
  id, table_id, token_opaco (firmado, no expone table_id en URL),
  nombre_comensal, estado (activa | cerrada), created_at, expires_at

carts                 -- 1 por table_session
  id, session_id, estado (abierto | confirmado)

cart_items
  id, cart_id, product_id, cantidad, precio_unitario

orders                -- 1 o más por mesa a lo largo de su ciclo de vida
  id, table_id, estado (abierta | bloqueada | pagada | cancelada),
  version (lock optimista), created_at

order_items            -- conserva origen del comensal para split posterior
  id, order_id, session_id, product_id, cantidad, precio_unitario,
  estado_cocina (pendiente | en_preparacion | listo | entregado | anulado),
  void_de (nullable, FK a order_items.id -- si este ítem reemplaza a uno anulado)

order_cancel_log       -- auditoría de cancelaciones (a nivel order)
  id, order_id, motivo, usuario, timestamp

order_item_void_log    -- auditoría de anulación/reemplazo de ítems individuales
  id, order_item_id, motivo, usuario, timestamp
```

## Reglas de negocio explícitas que el desarrollo debe respetar

- **Token de sesión:** opaco y firmado (JWT o similar) codificando `tenant_id` + `table_id`; nunca el `table_id` plano expuesto en la URL del QR, para evitar suplantación de mesa.
- **Descuento de inventario FEFO:** ocurre en el momento de insertar cada `order_item` (no en snapshot al consolidar). Las adiciones post-consolidación siguen el mismo camino transaccional, no uno paralelo.
- **Transición `abierta → bloqueada`:** disparada por caja al iniciar cobro, con lock optimista (columna `version`). Ninguna inserción de `order_item` es válida si `order.estado != 'abierta'`.
- **Regla de enrutamiento de inserciones:**
  ```
  al insertar order_item para mesa X:
    buscar order de mesa X con estado = 'abierta'
    si existe -> insertar ahí (sin importar qué session_id lo origina)
    si no existe -> crear nueva order 'abierta' para mesa X, insertar ahí
  ```
- **Cálculo de cuenta por comensal:** debe agregar `order_items` de **todas** las `orders` de la mesa (no solo una), filtrando por `session_id`.
- **Cierre/liberación de mesa:**
  ```
  al intentar table.estado = 'libre':
    si COUNT(orders WHERE table_id = X AND estado NOT IN ('pagada','cancelada')) > 0:
      bloquear con mensaje explícito (qué orden, cuántos ítems, monto)
    si = 0:
      permitir liberación
  ```
- **Cancelación de orden:** requiere motivo y usuario; dispara reversa de inventario ítem por ítem, mismo mecanismo que el descuento original invertido.
- **Facturación (fase 7):** debe soportar N facturas por mesa en un mismo cierre (una por cada `order` pagada), no asumir 1 factura = 1 mesa. Definir explícitamente si aplica cumplimiento DIAN por orden o a nivel de cierre de mesa completo.
- **Estado de cocina por ítem (KDS):** `pendiente → en_preparacion → listo → entregado`, actualizado en tiempo real desde la pantalla de cocina, independiente del `order.estado` de pago.
- **Modificación de un ítem ya insertado:**
  ```
  si order_item.estado_cocina == 'pendiente':
    permitir edición directa o cancelación con reversa de inventario
  si order_item.estado_cocina in ('en_preparacion', 'listo'):
    marcar order_item.estado_cocina = 'anulado' (no eliminar el registro)
    NO reversar inventario automáticamente (insumo ya consumido físicamente)
    crear order_item nuevo con void_de = id del anulado
    notificar a KDS: "anular ítem X" + "nuevo ítem Y" como eventos separados
  si order_item.estado_cocina == 'entregado':
    rechazar edición -> flujo de reclamo/reproceso, fuera de esta especificación
  ```
- **KDS como fuente de verdad del estado de cocina:** ningún otro componente (carrito, mesero, caja) escribe directamente `estado_cocina` salvo a través del evento correspondiente (nuevo ítem = `pendiente`, anulación = `anulado`). Cocina es quien transiciona `pendiente → en_preparacion → listo`.
- **Restricción dura sobre el bloqueo para cobro:** la transición `order.estado: abierta → bloqueada` se rechaza si existe algún `order_item` de esa orden con `estado_cocina IN ('pendiente', 'en_preparacion')`. Solo se permite bloquear para cobro cuando todos los ítems no anulados están en `listo` o `entregado`. Esta validación va en la misma transacción que el lock optimista de `version` — no es un chequeo previo separado que pueda quedar desactualizado entre la verificación y el `UPDATE`.

## Fases de implementación

**Fase 0 — Contrato de sesión y QR**
Definir formato del token firmado (tenant_id + table_id), TTL de sesión por comensal, y qué pasa con sesiones expiradas con carrito abierto.

**Fase 1 — Modelo de datos**
Crear `tables`, `table_sessions`, `carts`/`cart_items`, `orders` (con `estado` y `version`), `order_items` (con `session_id`), `order_cancel_log`. Migraciones Alembic dentro del esquema por tenant.

**Fase 2 — QR y menú público**
Endpoint público que resuelve tenant + mesa desde el token, sirve el menú de solo lectura sin autenticación de negocio.

**Fase 3 — Carrito por comensal**
Alta de `table_session` con nombre, alta/edición de `cart_items`, validación de disponibilidad de producto contra inventario/FEFO existente.

**Fase 4 — Consolidación por mesero**
Acción de mesero que agrupa `carts` confirmados en una `order` única para la mesa, generando `order_items` trazables por `session_id`, todos con `estado_cocina = 'pendiente'`. Aquí se define el punto de descuento de inventario (por ítem insertado, no snapshot).

**Fase 5 — Adiciones post-consolidación**
Mismo camino transaccional de fase 4 para nuevos `order_items`, condicionado a `order.estado = 'abierta'`, incluyendo la lógica de enrutamiento a orden-hija si no hay `order` abierta disponible.

**Fase 6 — Pantalla de cocina (KDS) y ciclo de vida del ítem**
Vista de cocina que consume `order_items` en `estado_cocina = 'pendiente'` (agrupados por mesa/orden, no por comensal), permite transición `pendiente → en_preparacion → listo → entregado`. Endpoint de anulación/reemplazo de ítem individual (`void` + creación de ítem nuevo con `void_de`), con notificación diferenciada a KDS en vez de edición silenciosa. Esta fase es prerrequisito de fase 7 porque bloqueo de cobro no debería iniciarse sin visibilidad de qué ítems siguen en preparación.

**Fase 7 — Bloqueo, cobro y liberación de mesa**
Transición `abierta → bloqueada` con lock optimista **y** validación en la misma transacción de que ningún `order_item` no anulado esté en `pendiente`/`en_preparacion` (caja no puede iniciar cobro con ítems todavía en cocina). Si la validación falla, caja recibe el detalle de qué ítems faltan por completar, no un error genérico. Cálculo de total agregando todas las `orders` de la mesa, split por `session_id`. Confirmación de pago → `pagada`. Reversa de inventario en `cancelada` con auditoría. Regla dura de liberación de mesa condicionada a cero órdenes no-terminales.

**Fase 8 — Facturación**
Transición `pagada → factura generada`, soportando múltiples facturas por mesa. Definir explícitamente si aplica DIAN y a qué nivel (por orden o por cierre de mesa).

## Pendientes cerrados en esta versión

1. **Ítems `anulado` se excluyen de la validación de fase 7 sin excepción.** Un ítem anulado nunca llega a `listo`; si no se excluye, ninguna orden con al menos una anulación podría cobrarse. La validación de fase 7 filtra `estado_cocina != 'anulado'` antes de exigir `listo`/`entregado` en el resto.
2. **Cobro con ítems atascados en cocina se resuelve por cancelación parcial (camino A), no por override de bloqueo.** Ver decisión #13. Un mecanismo de "forzar cobro con pendientes" (camino B: cobrar todo y entregar cuando esté listo, con permiso de supervisor) queda documentado como **fuera de alcance v1** — no implementar salvo que la operación real lo exija después de validar con camino A.

## Regla de fase 7 actualizada (validación de bloqueo)

```
al intentar order.estado: abierta -> bloqueada:
  pendientes = order_items WHERE order_id = X
               AND estado_cocina NOT IN ('listo', 'entregado', 'anulado')
  si pendientes.count > 0:
    rechazar con detalle (qué ítems, de qué comensal)
    -> mesero ofrece al comensal: anular esos ítems (reversa de inventario si aplica)
    -> reintentar bloqueo, ahora sin pendientes
  si pendientes.count == 0:
    proceder con lock optimista (version) y bloquear
```
