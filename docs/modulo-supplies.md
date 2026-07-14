# Módulo de Supplies (Inventario)

Resumen del modelo de datos y las reglas de negocio del inventario del POS. El módulo
gestiona **insumos a granel** (materia prima), su stock por **lotes**, el **consumo**
por receta (motor FEFO), las **reservas** transaccionales y la **disponibilidad**
calculada que alimenta el menú.

> Principio central: **el producto/variante es solo catálogo; lo que tiene stock y se
> descuenta es el _insumo_ (`Supply`)**. La unión entre ambos mundos es la **receta**
> (`Recipe`), que traduce "1 unidad vendible" a "N insumos en cantidad X".

---

## 1. Modelo de datos

### Entidades y relaciones

```
UnitMeasure ──< Supply ──< SupplyBatch ──< SupplyMovement
                  │                              │
                  │                              └─ (expense referencia order_id)
                  └──< StockReservation
                  └──< RecipeItem >── Recipe ──(dueño)── Variant  XOR  Modifier
                          │
                       UnitMeasure
```

### `Supply` — insumo a granel ([app/models/supply.py](../app/models/supply.py))
El sujeto real del inventario, medido en su **unidad base**.

| Campo | Tipo | Nota |
|---|---|---|
| `name` | str(150) | indexado |
| `unit_measure_id` | FK → UnitMeasure | unidad base (g, ml, und) |
| `stock_current` | Numeric(14,3) | saldo agregado (denormalizado) |
| `stock_min` | Numeric(14,3) | umbral para alertas |
| `track_expiry` | bool | si controla vencimiento (lácteos) |
| `active` | bool | |

### `SupplyBatch` — lote ([app/models/supply_batch.py](../app/models/supply_batch.py))
Cada compra/entrada. El consumo FEFO opera **sobre estos lotes**, no sobre `stock_current`.

| Campo | Tipo | Nota |
|---|---|---|
| `supply_id` | FK → Supply | |
| `code` | str(100) | referencia del lote |
| `quantity` | Numeric(14,3) | saldo **vigente del lote** (se decrementa al consumir) |
| `expires_at` | date? | indexado; null = no vence |
| `unit_cost` | Numeric(12,4) | costo de compra por unidad base |
| `received_at` | date | por defecto hoy |
| `active` | bool | |

### `SupplyMovement` — kardex ([app/models/supply_movement.py](../app/models/supply_movement.py))
Bitácora inmutable de cada movimiento de stock.

| Campo | Tipo | Nota |
|---|---|---|
| `supply_id` | FK → Supply | |
| `batch_id` | FK → SupplyBatch? | null en `adjust`/`waste` a nivel insumo |
| `quantity` | Numeric(14,3) | siempre **positiva** (el signo lo da `type`) |
| `type` | str(20) | `income` \| `expense` \| `adjust` \| `waste` (CHECK) |
| `reference_id` | UUID? | ej. `order_id` que originó el consumo |
| `reason` | str(255)? | |

### `StockReservation` — reserva ([app/models/stock_reservation.py](../app/models/stock_reservation.py))
Aparta disponibilidad **sin tocar lotes**; el consumo real ocurre al cobrar.

| Campo | Tipo | Nota |
|---|---|---|
| `order_id` / `order_item_id` | FK | origen de la reserva |
| `supply_id` | FK → Supply | |
| `quantity_reserved` | Numeric(14,3) | en unidad base |
| `status` | str(20) | `active` \| `released` \| `consumed` (CHECK) |
| `expires_at` | datetime? | TTL (30 min) |

### `Recipe` + `RecipeItem` — receta / BOM ([app/models/recipe.py](../app/models/recipe.py), [recipe_item.py](../app/models/recipe_item.py))
Define qué insumos y cuánto consume **una unidad vendible**.

- `Recipe`: dueño = **`variant_id` XOR `modifier_id`** (CHECK `= 1`), único por dueño
  (`uq__recipes__variant_id`, `uq__recipes__modifier_id`). `is_resale` marca reventa 1:1.
- `RecipeItem`: `supply_id` + `quantity` + `unit_measure_id` (la unidad del consumo,
  que puede diferir de la base del insumo pero **misma dimensión**).

### `UnitMeasure` — unidad ([app/models/unit_measure.py](../app/models/unit_measure.py))
`dimension` ∈ `MASS | VOLUME | COUNT` + `factor_to_base` (ej. kg→1000 g). Habilita
validar y convertir cantidades entre unidades de la misma dimensión
([app/core/units.py](../app/core/units.py) `convert()`).

---

## 2. Reglas de negocio

### Entradas de stock (compras)
- `POST /supplies/{id}/batches` crea el lote, **suma a `stock_current`** y registra un
  movimiento `income`. Es la única entrada de inventario.
- Si el insumo tiene `track_expiry=true`, el lote **exige `expires_at`** (422 si falta).

### Ajustes y mermas
- `POST /supplies/{id}/movements`:
  - `waste`: cantidad **> 0**, resta de `stock_current`; falla 400 si excede el saldo.
  - `adjust`: **delta con signo** (+/−) sobre `stock_current`.
- El movimiento guarda siempre `quantity` en valor absoluto; el `type` define el efecto.

### Recetas
- Una receta pertenece a **una variante XOR un modificador**, nunca a ambos ni a ninguno.
- `PUT .../recipe` hace **upsert reemplazando todos los items** (borra los previos).
- **Reventa 1:1** (`is_resale=true`): debe tener **exactamente 1 item**; se fuerza
  `quantity=1` y la unidad base del insumo (ignora lo enviado). Caso: producto que se
  revende igual que se compra (ej. cerveza embotellada).
- Receta normal: cada item valida que su unidad tenga la **misma dimensión** que la
  unidad base del insumo (no se puede mezclar g con ml) → 422.

### Gate de activación (regla clave del catálogo)
- Una **variante o modificador no se puede activar** (`active=true`) **sin una receta
  activa con items**. Definido en el catálogo/modifiers, pero es una regla del inventario:
  sin receta no hay forma de descontar stock, luego no es vendible.
  (Ver [catalog/router.py](../app/api/v1/catalog/router.py) y [modifiers/router.py](../app/api/v1/modifiers/router.py).)

### Consumo — motor FEFO atómico ([consumption.py](../app/api/v1/supplies/consumption.py))
- `consume_from_batches` es la **única** vía que escribe movimientos `expense`.
- Selecciona lotes con `SELECT ... FOR UPDATE` ordenados por **`expires_at` asc (nulls
  last), luego `received_at` asc** → política **FEFO** (First-Expired, First-Out).
- Si `track_expiry`, **excluye lotes vencidos** (`expires_at < hoy`).
- **Atómico**: si la suma de lotes vigentes < lo necesario, **no descuenta nada** y lanza
  `InsufficientStockError`. El `FOR UPDATE` evita sobreventa entre consumos concurrentes.
- `consume_sale` explota las recetas de las líneas, **agrega la necesidad por insumo**
  (convertida a unidad base) y delega en `consume_from_batches`. No hace commit.

### Reservas — reservar al pedir, consumir al cobrar ([reservations.py](../app/api/v1/orders/reservations.py))
Flujo de dos fases para el pedido por QR:
1. **`reserve_for_sale`** (al crear la orden): explota recetas, **bloquea la fila del
   insumo** (`FOR UPDATE`) y valida `disponibilidad ≥ necesidad`; crea reservas `active`
   con `expires_at = ahora + 30 min` (`RESERVATION_TTL_MIN`). No mueve lotes.
2. **`pay_order`** (al cobrar): consume las reservas activas vía `consume_from_batches`
   (FEFO) y las marca `consumed`.
3. **`release_reservations`** / **`release_expired`**: liberan reservas (`released`) sin
   generar movimientos; `release_expired` recupera reservas vencidas de órdenes `pending`.

### Disponibilidad calculada ([availability.py](../app/api/v1/menu/availability.py))
- `disponibilidad(insumo) = Σ lotes vigentes − Σ reservas activas` (solo lectura, sin locks).
- Una **variante está disponible** si _todos_ los insumos de su receta cubren lo que
  consume **1 unidad**. Sin receta con items ⇒ no disponible.
- Un **producto está disponible** si tiene ≥1 variante activa disponible.
- El menú (`GET /menu/products`, `/menu/products/{id}/variants`) **oculta** lo que no
  tenga disponibilidad real, además de exigir `is_menu=true` y `active=true`.

### Alertas
- `GET /supplies/alerts`: insumos con `stock_current < stock_min` (bajo mínimo) + lotes
  con `expires_at` dentro de una ventana de `days` (por defecto 15) y `quantity > 0`.

---

## 3. Endpoints

| Método | Ruta (`/api/v1`) | Rol | Descripción |
|---|---|---|---|
| GET | `/supplies` | user | Listar insumos |
| POST | `/supplies` | admin | Crear insumo |
| GET/PATCH | `/supplies/{id}` | user/admin | Ver / actualizar insumo |
| GET | `/supplies/alerts` | user | Bajo mínimo + por vencer |
| GET | `/supplies/{id}/batches` | user | Listar lotes |
| POST | `/supplies/{id}/batches` | admin | **Entrada de lote** (income) |
| POST | `/supplies/{id}/movements` | admin | **Ajuste / merma** |
| POST | `/supplies/consume` | admin | Consumo directo por receta (FEFO) |
| PUT/GET | `/variants/{id}/recipe` | admin/user | Receta de una variante |
| PUT/GET | `/modifiers/{id}/recipe` | admin/user | Receta de un modificador |

Autorización: escritura con `require_tenant_admin`, lectura con `get_current_user`.

---

## 4. Dependencias del módulo

- **Catálogo** ([catalog/](../app/api/v1/catalog/)): `Variant`/`Modifier` son dueños de las
  recetas; el gate de activación depende de que exista receta.
- **Órdenes** ([orders/](../app/api/v1/orders/)): usa `reserve_for_sale` / `pay_order` /
  `release_*` para reservar y consumir stock en el ciclo de la orden.
- **Menú** ([menu/](../app/api/v1/menu/)): filtra por `product_is_available` /
  `variant_is_available`.
- **Core**: `units.convert` (dimensión + `factor_to_base`), `InsufficientStockError`
  ([core/exceptions.py](../app/core/exceptions.py)), multi-tenant (`get_db`, `schema="tenant"`).

---

## 5. Flujos resumidos

**Compra / reabastecimiento**
```
POST /supplies/{id}/batches → +stock_current, movimiento income, lote disponible para FEFO
```

**Venta (pedido QR → cobro)**
```
crear orden  → reserve_for_sale  (valida disponibilidad, bloquea insumo, crea reservas active)
cobrar orden → pay_order         (consume_from_batches FEFO, reservas → consumed, movimientos expense)
cancelar/expirar → release_*     (reservas → released, sin tocar lotes)
```

**Merma / corrección**
```
POST /supplies/{id}/movements  type=waste (resta, >0)  |  type=adjust (delta con signo)
```

---

## 6. Invariantes a preservar

1. `stock_current` = Σ movimientos (income − expense − waste ± adjust). Toda escritura de
   stock debe pasar por un endpoint/función que también registre el `SupplyMovement`.
2. `expense` **solo** se escribe desde `consume_from_batches` (garantiza FEFO + atomicidad).
3. Una receta activa con items es prerequisito para que su dueño (variante/modificador)
   sea vendible.
4. La disponibilidad mostrada nunca debe superar `Σ lotes vigentes − Σ reservas activas`.
