# Prompt de implementación — Módulo de Caja (rediseño SkeiloPOS)

> **Para el desarrollador/agente:** implementa el **Módulo de Caja rediseñado** en este
> backend **extendiendo lo que ya existe** (no reescribas el módulo). Respeta las
> convenciones de [`CLAUDE.md`](../CLAUDE.md): lógica de negocio en **servicios Python**
> (no triggers/vistas), enums como **`String + CheckConstraint`** (no enums nativos de
> PG), dinero `Numeric(12,2)`, PK UUID, multi-tenant `schema-per-tenant` (header
> `x-tenant-host`), y usuario como **UUID soft-ref + snapshot `user_name`** (sin FK
> cross-schema).
>
> **Entregables:** (1) cambios de modelos SQLAlchemy, (2) **una migración Alembic**
> (con backfill), (3) schemas Pydantic, (4) endpoints en el router, (5) lógica en
> `service.py`, (6) tests. Cada fase de la §10 debe quedar verificable con los criterios
> de aceptación de la §9.

---

## 1. Contexto del sistema

POS multi-tenant (**FastAPI + SQLAlchemy 2 + PostgreSQL 16**, aislamiento
schema-per-tenant). El módulo de Caja controla el **efectivo por turno**: apertura con
fondo inicial, movimientos operativos (ingresos/egresos/retiros), las ventas cobradas en
el turno, y el **cierre con arqueo** (conteo de efectivo) que calcula la diferencia
esperado vs. contado.

Este documento nace del **rediseño de UI ya implementado en el frontend** (módulo
`cash-register` de la app Angular): pantallas de **apertura → dashboard de turno →
reporte de cierre**, con modales de movimiento y de arqueo por denominaciones. El backend
debe ofrecer los datos y operaciones que esas pantallas necesitan.

### Lo que YA existe (punto de partida)

| Pieza | Ubicación | Estado |
|---|---|---|
| `CashRegister` | [../app/models/cash_register.py](../app/models/cash_register.py) | Caja (nombre único, `active`). **Sin cambios.** |
| `CashShift` | [../app/models/cash_shift.py](../app/models/cash_shift.py) | Turno; índice parcial **1 abierto por caja**. **+`close_note`.** |
| `CashMovement` | [../app/models/cash_movement.py](../app/models/cash_movement.py) | Hoy `type IN ('in','out')`. **A extender.** |
| `CashCountDenomination` | [../app/models/cash_count_denomination.py](../app/models/cash_count_denomination.py) | Conteo por denominación. **Se reutiliza tal cual.** |
| Router / schemas / service | [../app/api/v1/cash/](../app/api/v1/cash/) | 6 endpoints; `reconcile()` en Python. **A extender.** |
| Ventas → caja | [../app/models/sale.py](../app/models/sale.py), [payment.py](../app/models/payment.py) | `Sale.cash_shift_id` (obligatorio) + `Payment` → `PaymentMethod`. |

**Reconciliación actual** ([../app/api/v1/cash/service.py](../app/api/v1/cash/service.py)):
`expected = opening_amount + cash_sales + cash_in − cash_out`, donde `cash_sales` = Σ
pagos de ventas `paid` del turno cuyo `PaymentMethod.is_cash = true`.

---

## 2. Alcance

**Dentro:**
- Movimientos manuales tipados: **ingreso / egreso / retiro**, con **categoría** y
  observación.
- Reconciliación **extendida**: ventas del turno **desglosadas por método**
  (efectivo / tarjeta / transferencia) además de ingresos/egresos/retiros.
- **Cierre con arqueo** por denominaciones (ya existe) + **observación obligatoria si hay
  diferencia**.
- **Listar movimientos** del turno (tabla / línea de tiempo del dashboard).
- **Reporte de cierre** consolidado (turno + reconciliación + movimientos +
  denominaciones + observación).
- **Consultar el turno abierto actual** de una caja (reemplaza el `localStorage` que hoy
  usa el frontend por falta de endpoint).

**Fuera:**
- El **simulador de ventas** del diseño (los 3 botones "Venta efectivo/tarjeta/
  transferencia") es solo demo del prototipo UI. Las ventas reales las crea el **módulo de
  Ventas** en el checkout ([../app/api/v1/sales/service.py](../app/api/v1/sales/service.py)),
  que ya asocia cada `Sale` al `cash_shift_id`. **No** se crean ventas desde Caja.
- Reapertura de turnos, multi-cajero por turno, y cuadres parciales intermedios.

---

## 3. Decisiones de producto (no reabrir sin justificación nueva)

1. **Las ventas NO se guardan en `cash_movements`.** Se **derivan** de
   `Sale → Payment → PaymentMethod` filtrando por `Sale.cash_shift_id` y
   `Sale.status = 'paid'`. `cash_movements` queda **solo** para movimientos manuales
   (ingreso/egreso/retiro). Esto evita duplicar la información que ya vive en Ventas.
2. **`retiro` ≠ `egreso`.** Ambos **restan** del efectivo esperado, pero se **reportan por
   separado**: `egreso` = gasto operativo (hielo, bolsas, transporte…); `retiro` = salida
   de efectivo del cajón hacia banco/caja fuerte (consignación, seguridad).
3. **Un solo turno abierto por caja** (invariante existente vía índice parcial único).
4. **Solo el efectivo suma al esperado.** `ventas_tarjeta` y `ventas_transferencia` son
   **informativas** (no entran en `expected`, porque no están en el cajón).
5. **Observación obligatoria si el arqueo no cuadra** (`difference != 0`).
6. **Inmutabilidad:** los movimientos y los turnos cerrados **no se editan ni se borran**.
   Correcciones = nuevo movimiento. Auditoría por `user_id` (soft-ref) + `user_name`.
7. **Categoría de movimiento = string libre** en el backend. Los catálogos sugeridos del
   diseño (p. ej. egreso: "Compra de hielo", "Bolsas"…) son una ayuda de UI; el backend
   solo exige que la categoría venga y la persiste.

---

## 4. Modelo de datos (cambios)

Todas las tablas viven en el esquema `tenant`. Convención de nombres SQL:
`ck__<tabla>__<nombre>`, `ix__<tabla>__<cols>`, etc.

### `cash_movements` — extender ([../app/models/cash_movement.py](../app/models/cash_movement.py))

| Campo | Cambio | Nota |
|---|---|---|
| `type` → `kind` | **Renombrar + reglas** | `String(20)` **CHECK IN (`ingreso` \| `egreso` \| `retiro`)**. Antes `type IN ('in','out')`. |
| `category` | **Nuevo** | `String(100)` NULL. Categoría del movimiento (p. ej. "Compra de hielo"). |
| `amount` | igual | `Numeric(12,2)` CHECK `> 0`. |
| `description` | igual | `String(255)`. Observación libre. |
| `user_id` / `occurred_at` | igual | soft-ref + timestamp. |

**Migración de datos:** `in → ingreso`, `out → egreso` (no existían retiros previos).

### `payment_methods` — extender ([../app/models/payment.py](../app/models/payment.py))

| Campo | Cambio | Nota |
|---|---|---|
| `type` | **Nuevo** | `String(20)` **CHECK IN (`cash` \| `card` \| `transfer` \| `other`)**, `server_default='other'`. Clasifica el método para el desglose de ventas del arqueo. |
| `is_cash` | se mantiene | Debe quedar **consistente** con `type` (`is_cash = (type == 'cash')`). |

**Backfill:** `is_cash = true → type = 'cash'`; el resto `type = 'other'` (el admin
reclasifica luego tarjeta/transferencia). Alternativa aceptable: derivar `is_cash` de
`type == 'cash'` y dejar de escribir `is_cash` a mano.

### `cash_shifts` — extender ([../app/models/cash_shift.py](../app/models/cash_shift.py))

| Campo | Cambio | Nota |
|---|---|---|
| `close_note` | **Nuevo** | `String(500)` NULL. Observación del arqueo (obligatoria si `difference != 0`). |

Se conservan: `opening_amount ≥ 0`, `status IN ('open','closed')`, índice parcial
`idx_open_shift_per_register` (1 abierto por caja), `opened_at`/`closed_at`,
`counted_amount`, `user_id`/`user_name`.

### `cash_count_denominations` — **sin cambios**

Ya modela `denomination` + `quantity` por turno (único por `(cash_shift_id, denomination)`).
El cierre suma `Σ denomination*quantity` como `counted_amount`.

---

## 5. Reglas de negocio

### Apertura de turno
- `opening_amount ≥ 0` (default 0).
- La caja debe existir (`get_or_404`). Si ya tiene un turno abierto → **409** ("La caja ya
  tiene un turno abierto") — garantizado por el índice parcial; capturar `IntegrityError`.
- Se snapshotean `user_id` y `user_name` del cajero que abre.

### Movimientos manuales (ingreso / egreso / retiro)
- Solo con turno **`open`** (si `closed` → **409** "El turno está cerrado").
- `amount > 0`; `kind ∈ {ingreso, egreso, retiro}`; **`category` requerida** (422 si falta).
- **Efecto en caja:** `ingreso (+)`, `egreso (−)`, `retiro (−)`.
- Inmutables (sin endpoints de edición/borrado).

### Reconciliación (arqueo en vivo y al cierre)

```
# Ventas del turno, agrupadas por clasificación del método de pago
ventas_por_metodo[t] = Σ Payment.amount
    de las ventas con Sale.cash_shift_id = shift.id
                   y Sale.status = 'paid'
    join PaymentMethod, agrupado por PaymentMethod.type   # t ∈ {cash, card, transfer, other}

ventas_efectivo      = ventas_por_metodo['cash']       # ÚNICA que suma a expected
ventas_tarjeta       = ventas_por_metodo['card']       # informativa
ventas_transferencia = ventas_por_metodo['transfer']   # informativa

# Movimientos manuales, agrupados por kind
ingresos = Σ cash_movements.amount  where kind = 'ingreso'
egresos  = Σ cash_movements.amount  where kind = 'egreso'
retiros  = Σ cash_movements.amount  where kind = 'retiro'

expected   = opening_amount + ventas_efectivo + ingresos − egresos − retiros
difference = counted_amount − expected      # solo si counted_amount != null
```

- `difference > 0` = **sobrante**; `< 0` = **faltante**; `0` = **cuadre perfecto**.

### Cierre con arqueo
- `get_or_404`; si ya está `closed` → **409** ("El turno ya está cerrado").
- `counted_amount` = valor enviado y/o **Σ `denomination*quantity`** de las
  denominaciones. Persistir una fila `CashCountDenomination` por denominación.
- Calcular `difference = counted_amount − expected`.
- **Si `difference != 0` ⇒ `close_note` (observación) es obligatoria → 422 si falta.**
- Set `status='closed'`, `closed_at = utcnow()`. Operación atómica (rollback ante error).

### Autorización
- **Crear caja**: `require_tenant_admin` (solo `ADMIN`).
- **Operar** (abrir turno, movimientos, cerrar, listar, reconciliar, reporte, turno
  actual): `get_current_user` (cualquier usuario del tenant, típicamente `CASHIER` o
  `ADMIN`). No hay guard dedicado de cajero — es el patrón del proyecto.

---

## 6. Endpoints (`/api/v1`)

| Método | Ruta | Rol | Descripción |
|---|---|---|---|
| GET | `/cash/registers` | user | Listar cajas. *(existe)* |
| POST | `/cash/registers` | admin | Crear caja (nombre único). *(existe)* |
| POST | `/cash/shifts/open` | user | Abrir turno con `opening_amount`. *(existe)* |
| **GET** | **`/cash/shifts/current`** | user | **Nuevo.** Turno abierto de una caja (`?cash_register_id=`); 404 si no hay. |
| POST | `/cash/shifts/{id}/movements` | user | Registrar movimiento. **Extender**: `kind` + `category`. |
| **GET** | **`/cash/shifts/{id}/movements`** | user | **Nuevo.** Listar movimientos del turno (orden `occurred_at` desc). |
| GET | `/cash/shifts/{id}/reconciliation` | user | Arqueo en vivo. **Extender** respuesta (ventas por método). |
| POST | `/cash/shifts/{id}/close` | user | Cerrar con arqueo. **Extender**: `close_note`. |
| **GET** | **`/cash/shifts/{id}/report`** | user | **Nuevo.** Reporte de cierre consolidado. |

### Schemas Pydantic (cambios) — [../app/api/v1/cash/schemas.py](../app/api/v1/cash/schemas.py)

```python
class CashMovementKind(str, Enum):
    INGRESO = "ingreso"; EGRESO = "egreso"; RETIRO = "retiro"

class CashMovementIn(BaseModel):
    kind: CashMovementKind
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    category: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=255)

class CashMovementResponse(BaseModel):          # from_attributes
    id: UUID; cash_shift_id: UUID; kind: str; amount: Decimal
    category: str | None; description: str | None; user_name: str | None; occurred_at: datetime

class ShiftClose(BaseModel):
    counted_amount: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    denominations: list[DenominationIn] = Field(default_factory=list)
    close_note: str | None = Field(None, max_length=500)   # obligatoria si difference != 0

class SalesByMethod(BaseModel):
    method_id: UUID; method_name: str; method_type: str; total: Decimal; count: int

class ReconciliationResponse(BaseModel):
    cash_shift_id: UUID; status: str
    opening_amount: Decimal
    ventas_efectivo: Decimal; ventas_tarjeta: Decimal; ventas_transferencia: Decimal
    sales_by_method: list[SalesByMethod]
    ingresos: Decimal; egresos: Decimal; retiros: Decimal
    expected: Decimal; counted_amount: Decimal | None; difference: Decimal | None

class ShiftReportResponse(BaseModel):
    shift: ShiftResponse
    reconciliation: ReconciliationResponse
    movements: list[CashMovementResponse]
    denominations: list[DenominationIn]
    close_note: str | None
```

> `cash_sales` puede conservarse como alias de `ventas_efectivo` por compatibilidad si el
> frontend real lo consume; documentar la deprecación.

---

## 7. Requerimientos funcionales (RF-CASH)

Extiende la sección 2.9 de [FUNCIONALIDADES.md](./FUNCIONALIDADES.md).

| ID | Actor | Requerimiento | Endpoint |
|---|---|---|---|
| RF-CASH-01 | Autenticado / Admin | Listar cajas / crear caja (nombre único) | `GET/POST /cash/registers` |
| RF-CASH-02 | Cajero | Abrir turno con monto inicial (una por caja; 409 si ya hay abierto) | `POST /cash/shifts/open` |
| RF-CASH-03 | Cajero | Registrar **ingreso/egreso/retiro** con **categoría** en el turno abierto | `POST /cash/shifts/{id}/movements` |
| RF-CASH-04 | Cajero | Cerrar turno con arqueo (contado y/o denominaciones) | `POST /cash/shifts/{id}/close` |
| RF-CASH-05 | Cajero | Ver reconciliación (esperado vs contado + diferencia) | `GET /cash/shifts/{id}/reconciliation` |
| **RF-CASH-06** | Cajero | Reconciliación con **ventas desglosadas por método** (efectivo/tarjeta/transferencia) | `GET /cash/shifts/{id}/reconciliation` |
| **RF-CASH-07** | Cajero | **Listar movimientos** del turno (tabla/línea de tiempo) | `GET /cash/shifts/{id}/movements` |
| **RF-CASH-08** | Cajero | Cierre exige **observación** si el arqueo **no cuadra** | `POST /cash/shifts/{id}/close` |
| **RF-CASH-09** | Cajero | **Reporte de cierre** consolidado del turno | `GET /cash/shifts/{id}/report` |
| **RF-CASH-10** | Cajero | Consultar el **turno abierto actual** de una caja | `GET /cash/shifts/current` |

---

## 8. Criterios de aceptación

- **RF-CASH-03:** `POST .../movements {kind:'egreso', amount:5000, category:'Bolsas'}` en
  turno abierto → 201; el movimiento aparece con `kind='egreso'`. `amount<=0` → 422;
  `category` vacía → 422; turno cerrado → 409.
- **RF-CASH-06:** con ventas del turno pagadas — 1 en efectivo ($30.000), 1 con tarjeta
  ($40.000), 1 por transferencia ($20.000) — la reconciliación devuelve
  `ventas_efectivo=30000`, `ventas_tarjeta=40000`, `ventas_transferencia=20000`, y
  `expected` **solo** suma los $30.000 de efectivo.
- **RF-CASH-07:** `GET .../movements` lista todos los movimientos del turno, orden
  descendente por `occurred_at`, con `kind`, `category`, `amount`, `user_name`.
- **RF-CASH-08:** cerrar con `counted_amount` que produce `difference != 0` **sin**
  `close_note` → 422. Con `close_note` → 200, turno `closed`, `close_note` persistida.
  `difference == 0` no exige nota.
- **RF-CASH-09:** `GET .../report` de un turno cerrado devuelve `shift`, `reconciliation`
  (con ventas por método), `movements`, `denominations` y `close_note`, todo coherente
  con lo registrado.
- **RF-CASH-10:** `GET /cash/shifts/current?cash_register_id=X` con turno abierto → ese
  turno; sin turno abierto → 404.
- **Arqueo (fórmula):** `expected = opening + ventas_efectivo + ingresos − egresos −
  retiros`; `difference = counted − expected`. Verificado con un caso numérico completo.

---

## 9. Fases de implementación

- **Fase 1 — Migración Alembic.** `cash_movements.type→kind` (widen `String(20)` + nuevo
  CHECK) y `+category`; `payment_methods.+type` (CHECK + backfill desde `is_cash`);
  `cash_shifts.+close_note`. Backfill `in→ingreso`, `out→egreso`. Importar modelos en
  `alembic/env.py` para autogenerate; correr por cada schema (`for_each_tenant_schema`).
- **Fase 2 — Movimientos.** Enum `CashMovementKind`, `CashMovementIn/Response`, endpoints
  crear (extendido) + **listar**. Validación de `category`.
- **Fase 3 — Reconciliación extendida.** `reconcile()` agrupa pagos por
  `PaymentMethod.type` y movimientos por `kind`; nueva `ReconciliationResponse` +
  `sales_by_method`.
- **Fase 4 — Cierre con observación.** `ShiftClose.close_note` + regla "obligatoria si
  `difference != 0`" (422).
- **Fase 5 — Reporte + turno actual.** `GET /cash/shifts/{id}/report` y
  `GET /cash/shifts/current`.
- **Fase 6 — Tests.** Unit del `reconcile()` (fórmula, agrupación por método) e
  integración de los flujos (abrir → movimientos → ventas → cerrar con/ sin diferencia →
  reporte). Actualizar `docs/FUNCIONALIDADES.md` y `CHANGELOG.md`.

---

## 10. Invariantes a preservar

1. **Un solo turno `open` por caja** (índice parcial único; no eliminar).
2. **Las ventas se derivan de `Payment`**, nunca se duplican como `cash_movements`.
3. **Solo el efectivo** (`PaymentMethod.type = 'cash'`) entra en `expected`.
4. La **lógica de reconciliación vive en `service.py`** (Python), no en vistas/triggers.
5. Enums como **`String + CheckConstraint`**; nada de enums nativos de PostgreSQL.
6. Usuario referenciado como **UUID soft-ref + `user_name` snapshot** (sin FK cross-schema).
7. **Movimientos y turnos cerrados son inmutables.**
8. `is_cash` y `PaymentMethod.type` **siempre consistentes** (`is_cash ⇔ type='cash'`).

---

## 11. Dependencias del módulo

- **Ventas** ([../app/api/v1/sales/](../app/api/v1/sales/)): `Sale.cash_shift_id`,
  `Payment`, `PaymentMethod` alimentan la reconciliación. El checkout ya exige turno
  abierto y asocia la venta al turno.
- **Core** ([../app/core/](../app/core/)): `get_db` (tenant), `get_current_user` /
  `require_tenant_admin`, `get_or_404` / `ensure_unique`, `Numeric(12,2)`.
- **Alembic** ([../alembic/](../alembic/)): `env.py::for_each_tenant_schema` para aplicar
  la migración a todos los esquemas de tenant.
