# Plan de refactorización — Módulo de productos e inventario (FastAPI)

## Diagnóstico — problemas identificados en el modelo actual

Este plan responde a problemas concretos detectados en el esquema actual, no a mejoras arquitectónicas genéricas. Un agente que implemente esto debe entender el problema antes que la solución, porque varias decisiones de diseño (por ejemplo, por qué la receta debe ser obligatoria incluso en reventa 1:1) solo tienen sentido a la luz de estos hallazgos.

### 1. No hay descuento de inventario en ningún punto del flujo

El sistema actual no ejecuta ningún descuento de `supplies` cuando se vende un producto. No es un bug de un punto específico — el mecanismo de consumo no existe todavía. Esto es la causa raíz de por qué "no se está llevando el inventario", y es el motivo por el que la Fase 0 no es una optimización sino la base sin la cual las demás fases no tienen de dónde partir.

### 2. Productos de reventa directa no tienen receta

Además de insumos que se transforman (helado + toppings), existen productos que se venden tal cual se compran (agua, gaseosa, snack empacado). Como el modelo asumía que todo pasa por `recipe_items` con conversión fraccional, estos productos de reventa no quedaron conectados a ningún mecanismo de descuento. Se resuelve tratando la reventa como una receta trivial 1:1 (Fase 1), no como un caso especial fuera del sistema de recetas.

### 3. Rotación constante de lotes con vencimiento, sin lógica FEFO

Los `supplies` manejan lote y vencimiento (`supply_batches`), y hay rotación constante — múltiples lotes abiertos simultáneamente, no un lote único por insumo. Sin una regla explícita de "primero el que vence primero" (FEFO) implementada de forma atómica, se vende de lotes frescos mientras lotes viejos caducan sin moverse. Esto es pérdida directa de inventario (merma), no un problema teórico, y es la razón por la que la Fase 0 exige `SELECT FOR UPDATE SKIP LOCKED` en vez de una simple consulta de lectura.

### 4. Ventana de tiempo entre pedido (QR) y cobro (caja)

El flujo actual separa la creación del pedido (mesa pide por QR) del cobro (caja, en un punto centralizado). Que el cobro sea "un solo punto" no elimina la condición de carrera: entre que se pide y se cobra, ese stock sigue apareciendo disponible para otras mesas. Con rotación constante de lotes, esto significa que dos pedidos distintos pueden asumir que van a consumir el mismo lote a punto de vencer. Se resuelve separando explícitamente reserva (al pedir) de consumo (al cobrar) — Fase 2.

### 5. Ambigüedad de disponibilidad: `active` manual vs stock real

`products.active` y `variants.active` son flags manuales que no reflejan si realmente hay stock vigente. Un producto puede seguir marcado como activo mientras todos sus lotes están vencidos o en cero, y el sistema lo seguiría ofreciendo en el menú. Se resuelve separando la decisión de negocio (`active`) del cálculo en tiempo real de disponibilidad (`is_available`) — Fase 3.

### 6. Recetas duplicadas o ambiguas por variante/modificador

Nada en el esquema actual impide que existan dos recetas activas para la misma variante o modificador, lo que generaría un costeo y consumo ambiguos al momento de vender. Más probable en productos de reventa, donde la receta es tan trivial (una sola línea) que se trata como configuración menor en vez de dato crítico. Se resuelve con integridad a nivel de base de datos, no de aplicación — Fase 4.

### 7. Redundancia y polimorfismo mal resuelto en el esquema

Independiente del problema de inventario, el esquema actual tiene deuda técnica que no pierde dinero hoy pero sí genera bugs de sincronización a futuro:

- `price` y `cost` existen tanto en `products` como en `variants`, sin que quede claro cuál es la fuente de verdad — y como todo producto (incluso "simple") ya tiene una variante `is_default`, la columna en `products` es redundante siempre.
- `recipes` usa dos columnas FK nullable (`variant_id`, `modifier_id`) para representar un dueño polimórfico, sin ninguna restricción que impida que ambas queden vacías o ambas llenas a la vez.
- `tax_links` vincula tanto `product_id` como `variant_id`, lo cual solo se justifica si existen casos reales de variantes con tasas de impuesto distintas dentro del mismo producto — a confirmar con negocio.

Este último punto se resuelve en la Fase 5, al final, porque es mantenibilidad, no pérdida de inventario activa.

---

## Consideraciones multi-tenant (una base de datos, múltiples schemas)

El sistema usa aislamiento por `schema_translate_map` (un schema PostgreSQL por tenant, misma base de datos). Esto no es un detalle de infraestructura aislado — afecta directamente cómo se implementan las Fases 0 y 4, y cómo se ejecutan las migraciones de todas las fases.

### Qué cambia respecto a un modelo de un solo schema

- **`consume_from_batches` debe recibir o resolver el tenant explícitamente.** El `SELECT ... FOR UPDATE SKIP LOCKED` bloquea filas dentro del schema que esté activo en la sesión al momento de abrir la transacción. Si el `schema_translate_map` se resuelve tarde (por ejemplo, en un middleware que corre después de que ya se abrió la transacción), existe el riesgo de bloquear o leer filas del schema equivocado. La función debe recibir el `tenant_schema` como parámetro explícito y verificar que la sesión ya esté vinculada a ese schema antes de ejecutar el `SELECT FOR UPDATE`, no asumirlo por contexto implícito de middleware.
- **Los índices únicos parciales y `CHECK` constraints de la Fase 4 no se propagan solos.** Cada tenant tiene sus propias tablas físicas (mismo nombre, distinto schema). Una migración de Alembic que no itere explícitamente sobre todos los schemas de tenant va a dejar el índice único solo en el schema donde corrió por defecto (o en el schema `public`/template si usan uno). Esto es exactamente el tipo de fallo silencioso que no se nota hasta que un tenant específico permite una receta duplicada y otro no.
- **Toda migración (Fases 0 a 5) debe ejecutarse dentro de un loop por tenant**, no como una migración única de Alembic. Si ya tienen un schema "template" o "shared" del cual se clonan los tenants nuevos, ese template también debe actualizarse para que los tenants creados después de este refactor nazcan con las reglas correctas.

### Ajuste concreto a la implementación

- Agregar (o confirmar que ya existe) un comando de migración que recorra la lista de schemas de tenant activos y aplique cada `ALTER TABLE` / `CREATE INDEX` / `CREATE FUNCTION` por separado, dentro del `search_path` de ese tenant.
- La función `consume_from_batches` y cualquier función SQL nueva (Fase 0) debe crearse en cada schema de tenant, no en `public`, salvo que el equipo ya tenga un mecanismo de funciones compartidas cross-schema — a confirmar, porque si `supplies`/`supply_batches` son tablas por-tenant, la función que las referencia por nombre sin schema calificado depende de que el `search_path` de la sesión esté correctamente resuelto al ejecutarse.
- Los tests de concurrencia e integridad de cada fase deben correr al menos contra dos schemas de tenant distintos simultáneamente, para detectar fugas de aislamiento (una transacción de un tenant bloqueando o leyendo filas de otro por un `search_path` mal resuelto), no solo contra un tenant aislado.

Este ajuste aplica principalmente a Fase 0 (función transaccional) y Fase 4 (constraints de integridad), que son las dos fases con objetos de base de datos nuevos. Las Fases 1, 2, 3 y 5 son cambios de lógica de aplicación y migraciones de columnas — siguen requiriendo el loop por tenant, pero no tienen el riesgo adicional de `search_path` en tiempo de ejecución.

---

## Contexto y restricción de orden

Este plan está diseñado para ejecutarse **en fases secuenciales, no en paralelo**. Cada fase depende de que la anterior esté completa y probada. Un agente o desarrollador que implemente esto debe cerrar la Fase 0 antes de tocar la Fase 1, sin excepción — el orden no es sugerencia arquitectónica, es priorización por pérdida de dinero real (merma por vencimiento y sobreventa concurrente).

No implementar fases fuera de orden aunque parezcan "más fáciles" o "más limpias" de resolver primero.

---

## Fase 0 — Motor de consumo FEFO transaccional

**Objetivo:** eliminar condiciones de carrera al descontar inventario, garantizando que siempre se consuma primero el lote más próximo a vencer.

### Regla de negocio

- Ningún descuento de `supplies` puede ocurrir sin pasar por una única función centralizada de consumo.
- La selección de lote es siempre FEFO (`expires_at ASC`), nunca FIFO por fecha de recepción.
- Una sola "unidad de venta" puede consumir de múltiples lotes si el primero no cubre la cantidad requerida.
- El bloqueo de filas debe evitar que dos transacciones concurrentes lean el mismo lote disponible antes de que la primera lo actualice.

### Implementación (FastAPI + SQLAlchemy + PostgreSQL)

- Crear función `consume_from_batches(db: Session, supply_id: UUID, quantity_needed: Decimal, reference_type: str, reference_id: UUID) -> list[SupplyMovement]`.
- Dentro de una transacción explícita, usar `SELECT ... FOR UPDATE SKIP LOCKED` ordenado por `expires_at ASC` sobre `supply_batches` con `quantity > 0` y `active = true`.
- Iterar lotes descontando hasta cubrir `quantity_needed`; generar una fila en `supply_movements` por cada lote tocado (`type = 'out'`, `batch_id` obligatorio, `reference_id`, `reference_type`).
- Si la suma de lotes disponibles no cubre `quantity_needed`, hacer rollback completo y lanzar excepción de dominio (`InsufficientStockError`), no descuento parcial.
- Prohibir cualquier otro camino de código que escriba en `supply_movements` de tipo `out` fuera de esta función (revisar y eliminar escrituras directas existentes en endpoints de caja/QR si las hay).

### Criterio de aceptación

- Test de concurrencia: dos requests simultáneos consumiendo el mismo lote no dejan `quantity` negativa ni doble descuento.
- Verificar que `SUM(supply_movements)` reconciliado siempre sea igual a `SUM(supply_batches.quantity)` inicial menos consumos.

---

## Fase 1 — Regla obligatoria de receta para todo ítem vendible

**Objetivo:** que la Fase 0 tenga siempre de dónde disparar, incluyendo productos de reventa directa.

### Regla de negocio

- Toda `variant` y todo `modifier` vendible **debe** tener al menos un `recipe_item` activo antes de poder marcarse como disponible para venta.
- Para productos de reventa directa (sin transformación), la receta es 1:1: `quantity = 1`, `unit_measure_id` igual al `unit_measure_id` del `supply` asociado. No se permite conversión de unidad en este caso.
- Para productos de transformación, se permite conversión fraccional validada contra `unit_measures.factor_to_base`.
- No se puede activar (`active = true`) una variante o modificador sin receta asociada.

### Implementación

- Agregar validación a nivel de servicio (no solo constraint de DB) en el endpoint de creación/activación de `variant` y `modifier`: si no existe `recipe_item` activo, bloquear la activación con error explícito.
- Agregar columna o inferencia `is_resale: bool` en `recipes` o derivarla de que el `supply` vinculado tenga `track_expiry` consistente con reventa (confirmar con negocio si aplica distinción o si toda receta 1:1 se trata igual).
- Endpoint de creación de receta debe forzar `quantity = 1` y misma unidad cuando se detecta patrón de reventa directa (un solo `recipe_item`, cantidad y unidad iguales al supply).

### Criterio de aceptación

- No es posible dejar una variante `active = true` sin receta en la base de datos (test de integración).
- Vender un producto de reventa directa dispara `consume_from_batches` con `quantity_needed = 1` en la unidad correcta.

---

## Fase 2 — Estados de orden: `reserved` → `consumed`

**Objetivo:** cerrar la ventana entre el pedido por QR y el cobro en caja, que hoy permite sobreventa o descuento prematuro.

### Regla de negocio

- Crear una orden desde el QR **no** ejecuta `consume_from_batches`. En su lugar, marca cantidad como `reserved` (reduce disponibilidad visible sin generar movimiento de salida definitivo).
- Confirmar el pago en caja es el único evento que ejecuta `consume_from_batches` y transiciona la reserva a `consumed`.
- Si una orden se cancela o expira sin cobrarse, la reserva se libera (sin generar movimiento de inventario).
- Cualquier modificación de ítems en caja (agregar/quitar productos o modificadores al momento de cobrar) debe pasar por la misma función `consume_from_batches` / lógica de reserva — no por un camino de "ajuste manual" separado.

### Implementación

- Nueva tabla `stock_reservations` (o campo de estado en la orden/ítem): `id, order_item_id, supply_id, quantity_reserved, status ('active','released','consumed'), created_at, expires_at`.
- Endpoint de creación de orden (QR) → crea reservas por cada `recipe_item` de cada ítem pedido.
- Endpoint de confirmación de pago (caja) → para cada reserva `active` asociada a la orden, ejecuta `consume_from_batches` y marca la reserva como `consumed`.
- Endpoint de cancelación/expiración de orden → marca reservas como `released`, sin tocar `supply_movements`.
- Disponibilidad visible en menú (Fase 3) debe descontar tanto lo `consumed` como lo `reserved`, no solo lo consumido.
- Job o trigger de expiración de reservas huérfanas (orden abandonada sin cobrar por tiempo X).

### Criterio de aceptación

- Simular dos QRs pidiendo el último lote disponible casi simultáneamente: solo uno logra reservar, el otro recibe error de disponibilidad.
- Cancelar una orden reservada no genera movimiento en `supply_movements`.

---

## Fase 3 — Disponibilidad calculada (`is_available`) separada de `active`

**Objetivo:** que el sistema deje de ofrecer productos sin stock vigente.

### Regla de negocio

- `active` es una decisión manual de negocio (el producto existe en el catálogo).
- `is_available` es un valor calculado, no editable directamente: `true` si existe stock vigente no vencido y no reservado en su totalidad (`SUM(supply_batches.quantity WHERE expires_at > now() AND active = true) - reservas activas > 0`).
- El menú (QR) solo debe mostrar como disponibles los productos con `active = true AND is_available = true`.

### Implementación

- Vista SQL o propiedad calculada en el servicio de catálogo (`get_menu`) que haga el cálculo en tiempo real; evaluar si conviene cachear con invalidación por evento de `supply_movements`/`stock_reservations` dado el volumen de rotación.
- No persistir `is_available` como columna editable manualmente desde el CRUD de producto.

### Criterio de aceptación

- Un producto cuyo único lote vence deja de aparecer disponible en el menú sin intervención manual.

---

## Fase 4 — Integridad de receta activa única

**Objetivo:** evitar recetas duplicadas o ambiguas por variante/modificador.

### Regla de negocio

- Solo puede existir una receta `active = true` por `variant_id` y una por `modifier_id` simultáneamente.

### Implementación

- Índice único parcial en PostgreSQL:
  ```sql
  CREATE UNIQUE INDEX uq_recipe_active_variant ON recipes (variant_id) WHERE active = true AND variant_id IS NOT NULL;
  CREATE UNIQUE INDEX uq_recipe_active_modifier ON recipes (modifier_id) WHERE active = true AND modifier_id IS NOT NULL;
  ```
- Agregar `CHECK` para evitar receta sin dueño o con doble dueño:
  ```sql
  ALTER TABLE recipes ADD CONSTRAINT chk_recipe_single_owner
    CHECK (num_nonnulls(variant_id, modifier_id) = 1);
  ```

### Criterio de aceptación

- Intentar insertar una segunda receta activa para la misma variante falla a nivel de base de datos, no solo de aplicación.

---

## Fase 5 — Limpieza de atributos redundantes (deuda técnica, no urgente)

**Objetivo:** eliminar ambigüedad de fuente de verdad. No afecta pérdida de inventario directamente; ejecutar después de las fases 0–4.

### Reglas de negocio

- `price` y `cost` viven únicamente en `variants` (incluida la variante `is_default` de productos simples). Eliminar de `products`.
- `products` contiene solo datos de catálogo/presentación: `name`, `description`, `category_id`, `type`, `is_menu`, `image_url`, `active`. Sin datos transaccionales.
- Confirmar con negocio si `unit_measure_id` a nivel de producto se mantiene como default heredado o se elimina (depende de si varía entre variantes del mismo producto).
- `tax_links.variant_id`: eliminar si no existe caso real de variantes con tasa distinta dentro del mismo producto; dejar el impuesto atado solo a `product_id` o `category_id`.

### Implementación

- Migración con backfill: copiar `products.price`/`cost` a la variante default donde falte, luego eliminar columnas.
- Actualizar todos los queries/serializers que hoy leen precio desde `products` para leer desde `variants`.

### Criterio de aceptación

- Ningún endpoint depende de `products.price` o `products.cost` tras la migración.

---

## Reglas transversales (aplican a todas las fases)

1. Ninguna escritura de inventario (`supply_movements`) fuera de `consume_from_batches`.
2. Ninguna reserva/consumo sin receta activa asociada (depende de Fase 1).
3. Toda migración de esquema debe ir acompañada de backfill de datos existentes antes de agregar constraints `NOT NULL` o `UNIQUE`.
4. Cada fase requiere test de concurrencia o integridad antes de pasar a la siguiente — no basta con test unitario feliz.

## Orden de ejecución obligatorio

`Fase 0 → Fase 1 → Fase 2 → Fase 3 → Fase 4 → Fase 5`

No comenzar una fase sin que la anterior tenga migración aplicada, tests pasando y código de escritura antigua removido (no dejar el camino viejo "por si acaso" — eso reintroduce el bug que se está cerrando).
