# Changelog

Todos los cambios notables de este proyecto se documentan en este archivo.

El formato se basa en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el proyecto sigue [Versionado Semántico](https://semver.org/lang/es/).

## [Unreleased]

### Caja (rediseño del módulo)
- **Movimientos tipados**: `cash_movements.type` → `kind`
  (`ingreso`/`egreso`/`retiro`) + `category`; `retiro` (salida a banco/caja
  fuerte) se reporta aparte del `egreso` (gasto operativo). `description` pasa a
  opcional; se snapshotea `user_name` del cajero. Movimientos inmutables.
- **Reconciliación extendida**: ventas del turno desglosadas por método de pago
  (`ventas_efectivo`/`ventas_tarjeta`/`ventas_transferencia` + `sales_by_method`)
  y movimientos por `kind`. `expected = apertura + ventas_efectivo + ingresos −
  egresos − retiros`; solo el efectivo suma al esperado.
- **Métodos de pago**: `payment_methods.type`
  (`cash`/`card`/`transfer`/`other`, CHECK) para clasificar el desglose;
  `is_cash` y `type` se mantienen consistentes (`is_cash ⇔ type='cash'`).
- **Cierre con observación**: `cash_shifts.close_note`; obligatoria (422) si el
  arqueo no cuadra (`difference != 0`).
- **Endpoints nuevos**: `GET /cash/shifts/current` (turno abierto de una caja),
  `GET /cash/shifts/{id}/movements` (listado), `GET /cash/shifts/{id}/report`
  (reporte de cierre consolidado).
- Corrige el doble conteo del cierre cuando llegaban `counted_amount` y
  `denominations` a la vez (ahora las denominaciones tienen prioridad).
- Migración Alembic incremental con backfill (`in→ingreso`, `out→egreso`,
  `is_cash→type='cash'`), aplicada por cada schema de tenant.

## [1.0.0] - 2026-07-17

Primera versión estable. Backend POS multi-tenant (schema-per-tenant) sobre
FastAPI + PostgreSQL 16, con el flujo completo de gestión de mesas por QR.

### Núcleo / plataforma
- Multi-tenancy schema-per-tenant con resolución por header `x-tenant-host` y
  `schema_translate_map`; schema `shared` para tenants/usuarios/roles.
- Bootstrap automático: creación del schema `shared`, siembra de roles
  (`SUPER_ADMIN`, `ADMIN`, `CASHIER`) y super admin global en el primer arranque.
- Autenticación JWT (access/refresh) con blocklist en Redis; roles y guardias
  de tenant/super-admin.
- CLI de gestión de tenants; migraciones Alembic por tenant.
- Despliegue con Docker Compose (api, postgres, redis, worker Celery).

### Dominio
- **Catálogo:** categorías, productos, variantes (precio + receta), grupos de
  opciones y opciones; unidades de medida.
- **Inventario:** stock único por insumo, kardex de movimientos, proveedores,
  compras (recepción suma stock).
- **Caja:** registradoras, turnos, movimientos y arqueo por denominaciones.
- **Ventas:** checkout con snapshots inmutables, pagos y métodos de pago,
  descuento de inventario por receta y opciones.
- **Imágenes de producto:** subida a Cloudflare R2 mediante URLs prefirmadas.

### Gestión de mesas por QR (Fases 0–8)
- **Tokens firmados** de QR (tenant + mesa) y de sesión de comensal (ventana
  deslizante configurable, tope absoluto), aislados de los JWT de staff.
- **Menú público** por token de QR, sin autenticación de negocio.
- **Carrito por comensal:** sesiones por comensal, alta/edición de líneas con
  opciones y chequeo preventivo de disponibilidad de inventario.
- **Consolidación por el mesero:** agrupa carritos en una orden de mesa,
  trazabilidad por comensal (`session_id`) y **descuento real de inventario**.
- **Adiciones post-consolidación** con enrutamiento a orden-hija cuando la orden
  está en cobro; add directo de ítem por el mesero.
- **KDS (pantalla de cocina):** estados por ítem
  (`pendiente → en_preparacion → listo → entregado`), anulación/reemplazo de
  ítem con reversa de inventario cuando aplica y auditoría.
- **Cobro:** bloqueo con lock optimista y validación de cocina, cuenta con
  *split* por comensal, pago (genera venta ligada a la orden, sin doble
  descuento), cancelación con reversa de inventario y liberación de mesa con
  regla dura.
- **Facturación:** factura interna con consecutivo, N facturas por mesa (una por
  orden pagada); emisión por orden y por cierre de mesa.

### Fuera de alcance de v1
- Integración de facturación electrónica DIAN (el modelo queda *DIAN-ready*:
  campos `cufe`/`dian_status` y numeración por prefijo).
- Notas crédito / anulación fiscal.
- Notificaciones push al KDS (v1 funciona por *polling*).

[1.0.0]: https://github.com/deimerhdz/pos-backend/releases/tag/v1.0.0
