# Estado actual de la base de datos

> Documento **autogenerado** por introspección de la DB en vivo (PostgreSQL 16). Modelo multi-tenant *schema-per-tenant*: un schema `shared` con la identidad global y un schema por tenant con el dominio del POS (todas las tablas de tenant son idénticas entre schemas).

- **Schema `shared`**: 3 tablas (identidad: tenants, usuarios, roles).
- **Schema de tenant** (p. ej. `heladeria`): 33 tablas (dominio POS).
- Tenants actuales con schema propio: `heladeria`, `heladeria2`, `heladeria3`.

Convención de nombres: `pk__<tabla>`, `fk__<tabla>__<cols>__<ref>`, `uq__<tabla>__<cols>`, `ck__<tabla>__<nombre>`, `ix__<tabla>__<cols>`. Los enums se modelan como `String + CHECK` (no enums nativos de PG); el dinero es `numeric(12,2)`; PKs UUID salvo `shared.tenants` (entero).

Las columnas de tenant que referencian **quién actuó** (`user_id`) son *soft-ref* a `shared.users.id` **sin FK cross-schema** (se acompañan de un snapshot `user_name`).

---

## Schema `shared`

#### `tenants`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `id` 🔑 | integer | no | nextval('shared.tenants_id_seq' |
| `name` | character varying(255) | no |  |
| `schema` | character varying(255) | no |  |
| `plan` | character varying(100) | no |  |
| `host` | character varying(255) | no |  |
| `created_at` | timestamp without time zone | no | now() |
| `updated_at` | timestamp without time zone | sí |  |
| `logo_url` | character varying(500) | sí |  |

**Únicos:** `host`; `schema`

**Índices:**
- `ix__tenants__name`: CREATE UNIQUE INDEX ix__tenants__name ON shared.tenants USING btree (name)

#### `roles`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `name` | character varying(150) | no |  |
| `active` | boolean | no |  |
| `id` 🔑 | uuid | no |  |
| `created_at` | timestamp without time zone | no | now() |
| `updated_at` | timestamp without time zone | sí |  |

#### `users`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `name` | character varying(150) | no |  |
| `email` | character varying(255) | no |  |
| `password_hash` | character varying(255) | no |  |
| `phone` | character varying(20) | sí |  |
| `active` | boolean | no |  |
| `must_change_password` | boolean | no |  |
| `role_id` | uuid | no |  |
| `tenant_id` | integer | sí |  |
| `id` 🔑 | uuid | no |  |
| `created_at` | timestamp without time zone | no | now() |
| `updated_at` | timestamp without time zone | sí |  |

**Relaciones (FK):**
- `role_id` → `shared.roles(id)`
- `tenant_id` → `shared.tenants(id)`

**Únicos:** `tenant_id,email`

**Índices:**
- `ix__users__email`: CREATE INDEX ix__users__email ON shared.users USING btree (email)
- `ix__users__tenant_id`: CREATE INDEX ix__users__tenant_id ON shared.users USING btree (tenant_id)

---

## Schema de tenant (plantilla, tomada de `heladeria`)

### Catálogo

#### `categories`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `name` | character varying(255) | no |  |
| `description` | character varying(255) | sí |  |
| `active` | boolean | no |  |
| `id` 🔑 | uuid | no |  |
| `created_at` | timestamp without time zone | no | now() |
| `updated_at` | timestamp without time zone | sí |  |

**Índices:**
- `ix_tenant_categories_name`: CREATE UNIQUE INDEX ix_tenant_categories_name ON heladeria.categories USING btree (name)

#### `unit_measures`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `name` | character varying(255) | no |  |
| `abbreviation` | character varying(50) | no |  |
| `active` | boolean | no |  |
| `id` 🔑 | uuid | no |  |
| `created_at` | timestamp without time zone | no | now() |
| `updated_at` | timestamp without time zone | sí |  |

**Únicos:** `name`

**Índices:**
- `ix_tenant_unit_measures_abbreviation`: CREATE UNIQUE INDEX ix_tenant_unit_measures_abbreviation ON heladeria.unit_measures USING btree (abbreviation)

#### `products`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `category_id` | uuid | no |  |
| `name` | character varying(255) | no |  |
| `description` | character varying(500) | sí |  |
| `preparation_type` | character varying(20) | no | 'prepared' |
| `image_url` | character varying(500) | sí |  |
| `active` | boolean | no |  |
| `id` 🔑 | uuid | no |  |
| `created_at` | timestamp without time zone | no | now() |
| `updated_at` | timestamp without time zone | sí |  |

**Relaciones (FK):**
- `category_id` → `heladeria.categories(id)`

**Únicos:** `category_id,name`

**Checks:**
- `ck__products__ck_product_preparation_type`: CHECK (((preparation_type)::text = ANY ((ARRAY['prepared'::character varying, 'packaged'::character varying])::text[])))

**Índices:**
- `ix_tenant_products_category_id`: CREATE INDEX ix_tenant_products_category_id ON heladeria.products USING btree (category_id)

#### `product_variants`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `product_id` | uuid | no |  |
| `name` | character varying(255) | no | 'Single' |
| `sku` | character varying(100) | sí |  |
| `price` | numeric(12,2) | no | '0' |
| `active` | boolean | no |  |
| `id` 🔑 | uuid | no |  |
| `created_at` | timestamp without time zone | no | now() |
| `updated_at` | timestamp without time zone | sí |  |

**Relaciones (FK):**
- `product_id` → `heladeria.products(id)` · ON DELETE CASCADE

**Únicos:** `sku`; `product_id,name`

**Checks:**
- `ck__product_variants__ck_product_variant_price_positive`: CHECK ((price >= (0)::numeric))

**Índices:**
- `ix_tenant_product_variants_product_id`: CREATE INDEX ix_tenant_product_variants_product_id ON heladeria.product_variants USING btree (product_id)

#### `recipe_items`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `product_variant_id` | uuid | no |  |
| `inventory_item_id` | uuid | no |  |
| `quantity` | numeric(12,3) | no |  |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `inventory_item_id` → `heladeria.inventory_items(id)`
- `product_variant_id` → `heladeria.product_variants(id)` · ON DELETE CASCADE

**Únicos:** `product_variant_id,inventory_item_id`

**Checks:**
- `ck__recipe_items__ck_recipe_item_qty_positive`: CHECK ((quantity > (0)::numeric))

**Índices:**
- `ix_tenant_recipe_items_inventory_item_id`: CREATE INDEX ix_tenant_recipe_items_inventory_item_id ON heladeria.recipe_items USING btree (inventory_item_id)
- `ix_tenant_recipe_items_product_variant_id`: CREATE INDEX ix_tenant_recipe_items_product_variant_id ON heladeria.recipe_items USING btree (product_variant_id)

#### `option_groups`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `name` | character varying(255) | no |  |
| `min_select` | integer | no | 0 |
| `max_select` | integer | no | 1 |
| `id` 🔑 | uuid | no |  |

**Únicos:** `name`

**Checks:**
- `ck__option_groups__ck_option_group_max_ge_min`: CHECK ((max_select >= min_select))
- `ck__option_groups__ck_option_group_min_select`: CHECK ((min_select >= 0))

#### `options`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `option_group_id` | uuid | no |  |
| `name` | character varying(255) | no |  |
| `extra_price` | numeric(12,2) | no | '0' |
| `inventory_item_id` | uuid | sí |  |
| `item_quantity` | numeric(12,3) | no | '0' |
| `active` | boolean | no |  |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `inventory_item_id` → `heladeria.inventory_items(id)`
- `option_group_id` → `heladeria.option_groups(id)` · ON DELETE CASCADE

**Únicos:** `option_group_id,name`

**Checks:**
- `ck__options__ck_option_extra_price_positive`: CHECK ((extra_price >= (0)::numeric))

**Índices:**
- `ix_tenant_options_option_group_id`: CREATE INDEX ix_tenant_options_option_group_id ON heladeria.options USING btree (option_group_id)

#### `product_option_groups`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `product_id` | uuid | no |  |
| `option_group_id` | uuid | no |  |
| `min_select` | integer | no | 0 |
| `max_select` | integer | no | 1 |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `option_group_id` → `heladeria.option_groups(id)`
- `product_id` → `heladeria.products(id)` · ON DELETE CASCADE

**Únicos:** `product_id,option_group_id`

**Checks:**
- `ck__product_option_groups__ck_product_option_group_max_ge_min`: CHECK ((max_select >= min_select))

**Índices:**
- `ix_tenant_product_option_groups_option_group_id`: CREATE INDEX ix_tenant_product_option_groups_option_group_id ON heladeria.product_option_groups USING btree (option_group_id)
- `ix_tenant_product_option_groups_product_id`: CREATE INDEX ix_tenant_product_option_groups_product_id ON heladeria.product_option_groups USING btree (product_id)

### Inventario

#### `inventory_items`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `name` | character varying(255) | no |  |
| `unit_measure_id` | uuid | no |  |
| `type` | character varying(20) | no | 'raw_material' |
| `current_stock` | numeric(12,3) | no | '0' |
| `min_stock` | numeric(12,3) | no | '0' |
| `unit_cost` | numeric(12,2) | no | '0' |
| `active` | boolean | no |  |
| `id` 🔑 | uuid | no |  |
| `created_at` | timestamp without time zone | no | now() |
| `updated_at` | timestamp without time zone | sí |  |

**Relaciones (FK):**
- `unit_measure_id` → `heladeria.unit_measures(id)`

**Checks:**
- `ck__inventory_items__ck_inventory_item_type`: CHECK (((type)::text = ANY ((ARRAY['raw_material'::character varying, 'packaged'::character varying])::text[])))

**Índices:**
- `ix_tenant_inventory_items_name`: CREATE UNIQUE INDEX ix_tenant_inventory_items_name ON heladeria.inventory_items USING btree (name)
- `ix_tenant_inventory_items_unit_measure_id`: CREATE INDEX ix_tenant_inventory_items_unit_measure_id ON heladeria.inventory_items USING btree (unit_measure_id)

#### `inventory_movements`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `inventory_item_id` | uuid | no |  |
| `type` | character varying(20) | no |  |
| `quantity` | numeric(12,3) | no |  |
| `reason` | character varying(255) | sí |  |
| `reference_type` | character varying(50) | sí |  |
| `reference_id` | uuid | sí |  |
| `user_id` | uuid | sí |  |
| `moved_at` | timestamp without time zone | no | now() |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `inventory_item_id` → `heladeria.inventory_items(id)`

**Checks:**
- `ck__inventory_movements__ck_inventory_movement_qty_positive`: CHECK ((quantity > (0)::numeric))
- `ck__inventory_movements__ck_inventory_movement_type`: CHECK (((type)::text = ANY ((ARRAY['in'::character varying, 'out'::character varying, 'adjustment'::character varying])::text[])))

**Índices:**
- `idx_invmov_ref`: CREATE INDEX idx_invmov_ref ON heladeria.inventory_movements USING btree (reference_type, reference_id)
- `ix_tenant_inventory_movements_inventory_item_id`: CREATE INDEX ix_tenant_inventory_movements_inventory_item_id ON heladeria.inventory_movements USING btree (inventory_item_id)

#### `suppliers`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `name` | character varying(255) | no |  |
| `tax_id` | character varying(50) | sí |  |
| `phone` | character varying(50) | sí |  |
| `email` | character varying(255) | sí |  |
| `active` | boolean | no |  |
| `id` 🔑 | uuid | no |  |
| `created_at` | timestamp without time zone | no | now() |
| `updated_at` | timestamp without time zone | sí |  |

#### `purchases`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `supplier_id` | uuid | sí |  |
| `user_id` | uuid | sí |  |
| `invoice_number` | character varying(100) | sí |  |
| `total` | numeric(12,2) | no | '0' |
| `purchased_at` | timestamp without time zone | no | now() |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `supplier_id` → `heladeria.suppliers(id)`

**Índices:**
- `ix_tenant_purchases_supplier_id`: CREATE INDEX ix_tenant_purchases_supplier_id ON heladeria.purchases USING btree (supplier_id)

#### `purchase_items`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `purchase_id` | uuid | no |  |
| `inventory_item_id` | uuid | no |  |
| `quantity` | numeric(12,3) | no |  |
| `unit_cost` | numeric(12,2) | no | '0' |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `inventory_item_id` → `heladeria.inventory_items(id)`
- `purchase_id` → `heladeria.purchases(id)` · ON DELETE CASCADE

**Checks:**
- `ck__purchase_items__ck_purchase_item_qty_positive`: CHECK ((quantity > (0)::numeric))

**Índices:**
- `ix_tenant_purchase_items_inventory_item_id`: CREATE INDEX ix_tenant_purchase_items_inventory_item_id ON heladeria.purchase_items USING btree (inventory_item_id)
- `ix_tenant_purchase_items_purchase_id`: CREATE INDEX ix_tenant_purchase_items_purchase_id ON heladeria.purchase_items USING btree (purchase_id)

### Órdenes / QR / Carrito

#### `dining_tables`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `number` | integer | no |  |
| `name` | character varying(255) | sí |  |
| `qr_token` | uuid | no |  |
| `active` | boolean | no |  |
| `status` | character varying(10) | no | 'libre' |
| `id` 🔑 | uuid | no |  |

**Únicos:** `number`; `qr_token`

**Checks:**
- `ck__dining_tables__ck_dining_table_status`: CHECK (((status)::text = ANY ((ARRAY['libre'::character varying, 'ocupada'::character varying])::text[])))

#### `dining_sessions`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `dining_table_id` | uuid | no |  |
| `customer_name` | character varying(255) | no |  |
| `status` | character varying(10) | no | 'open' |
| `opened_at` | timestamp without time zone | no | now() |
| `expires_at` | timestamp without time zone | sí |  |
| `closed_at` | timestamp without time zone | sí |  |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `dining_table_id` → `heladeria.dining_tables(id)`

**Checks:**
- `ck__dining_sessions__ck_dining_session_status`: CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'closed'::character varying])::text[])))

**Índices:**
- `ix_tenant_dining_sessions_dining_table_id`: CREATE INDEX ix_tenant_dining_sessions_dining_table_id ON heladeria.dining_sessions USING btree (dining_table_id)

#### `carts`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `session_id` | uuid | no |  |
| `status` | character varying(12) | no | 'abierto' |
| `created_at` | timestamp without time zone | no | now() |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `session_id` → `heladeria.dining_sessions(id)`

**Checks:**
- `ck__carts__ck_cart_status`: CHECK (((status)::text = ANY ((ARRAY['abierto'::character varying, 'confirmado'::character varying, 'abandonado'::character varying])::text[])))

**Índices:**
- `idx_open_cart_per_session`: CREATE UNIQUE INDEX idx_open_cart_per_session ON heladeria.carts USING btree (session_id) WHERE ((status)::text = 'abierto'::text)
- `ix_tenant_carts_session_id`: CREATE INDEX ix_tenant_carts_session_id ON heladeria.carts USING btree (session_id)

#### `cart_items`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `cart_id` | uuid | no |  |
| `product_variant_id` | uuid | no |  |
| `quantity` | integer | no | 1 |
| `unit_price` | numeric(12,2) | no | '0' |
| `notes` | character varying(500) | sí |  |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `cart_id` → `heladeria.carts(id)` · ON DELETE CASCADE
- `product_variant_id` → `heladeria.product_variants(id)`

**Checks:**
- `ck__cart_items__ck_cart_item_quantity_positive`: CHECK ((quantity > 0))

**Índices:**
- `ix_tenant_cart_items_cart_id`: CREATE INDEX ix_tenant_cart_items_cart_id ON heladeria.cart_items USING btree (cart_id)
- `ix_tenant_cart_items_product_variant_id`: CREATE INDEX ix_tenant_cart_items_product_variant_id ON heladeria.cart_items USING btree (product_variant_id)

#### `cart_item_options`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `cart_item_id` | uuid | no |  |
| `option_id` | uuid | no |  |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `cart_item_id` → `heladeria.cart_items(id)` · ON DELETE CASCADE
- `option_id` → `heladeria.options(id)`

**Únicos:** `cart_item_id,option_id`

**Índices:**
- `ix_tenant_cart_item_options_cart_item_id`: CREATE INDEX ix_tenant_cart_item_options_cart_item_id ON heladeria.cart_item_options USING btree (cart_item_id)
- `ix_tenant_cart_item_options_option_id`: CREATE INDEX ix_tenant_cart_item_options_option_id ON heladeria.cart_item_options USING btree (option_id)

#### `customer_orders`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `dining_session_id` | uuid | sí |  |
| `dining_table_id` | uuid | sí |  |
| `customer_name` | character varying(255) | sí |  |
| `channel` | character varying(10) | no | 'qr' |
| `status` | character varying(12) | no | 'abierta' |
| `version` | integer | no | 0 |
| `user_id` | uuid | sí |  |
| `notes` | character varying(500) | sí |  |
| `created_at` | timestamp without time zone | no | now() |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `dining_session_id` → `heladeria.dining_sessions(id)`
- `dining_table_id` → `heladeria.dining_tables(id)`

**Checks:**
- `ck__customer_orders__ck_customer_order_channel`: CHECK (((channel)::text = ANY ((ARRAY['qr'::character varying, 'counter'::character varying, 'waiter'::character varying])::text[])))
- `ck__customer_orders__ck_customer_order_status`: CHECK (((status)::text = ANY ((ARRAY['abierta'::character varying, 'bloqueada'::character varying, 'pagada'::character varying, 'cancelada'::character varying])::text[])))

**Índices:**
- `idx_open_order_per_table`: CREATE UNIQUE INDEX idx_open_order_per_table ON heladeria.customer_orders USING btree (dining_table_id) WHERE ((status)::text = 'abierta'::text)
- `ix_tenant_customer_orders_dining_session_id`: CREATE INDEX ix_tenant_customer_orders_dining_session_id ON heladeria.customer_orders USING btree (dining_session_id)

#### `order_items`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `order_id` | uuid | no |  |
| `session_id` | uuid | sí |  |
| `product_variant_id` | uuid | no |  |
| `quantity` | integer | no | 1 |
| `unit_price` | numeric(12,2) | no | '0' |
| `estado_cocina` | character varying(15) | no | 'pendiente' |
| `void_de` | uuid | sí |  |
| `notes` | character varying(500) | sí |  |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `order_id` → `heladeria.customer_orders(id)` · ON DELETE CASCADE
- `product_variant_id` → `heladeria.product_variants(id)`
- `session_id` → `heladeria.dining_sessions(id)`
- `void_de` → `heladeria.order_items(id)`

**Checks:**
- `ck__order_items__ck_order_item_estado_cocina`: CHECK (((estado_cocina)::text = ANY ((ARRAY['pendiente'::character varying, 'en_preparacion'::character varying, 'listo'::character varying, 'anulado'::character varying])::text[])))
- `ck__order_items__ck_order_item_quantity_positive`: CHECK ((quantity > 0))

**Índices:**
- `ix_tenant_order_items_order_id`: CREATE INDEX ix_tenant_order_items_order_id ON heladeria.order_items USING btree (order_id)
- `ix_tenant_order_items_product_variant_id`: CREATE INDEX ix_tenant_order_items_product_variant_id ON heladeria.order_items USING btree (product_variant_id)
- `ix_tenant_order_items_session_id`: CREATE INDEX ix_tenant_order_items_session_id ON heladeria.order_items USING btree (session_id)

#### `order_item_options`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `order_item_id` | uuid | no |  |
| `option_id` | uuid | no |  |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `option_id` → `heladeria.options(id)`
- `order_item_id` → `heladeria.order_items(id)` · ON DELETE CASCADE

**Únicos:** `order_item_id,option_id`

**Índices:**
- `ix_tenant_order_item_options_option_id`: CREATE INDEX ix_tenant_order_item_options_option_id ON heladeria.order_item_options USING btree (option_id)
- `ix_tenant_order_item_options_order_item_id`: CREATE INDEX ix_tenant_order_item_options_order_item_id ON heladeria.order_item_options USING btree (order_item_id)

#### `order_cancel_logs`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `order_id` | uuid | no |  |
| `motivo` | character varying(500) | no |  |
| `user_id` | uuid | no |  |
| `user_name` | character varying(255) | sí |  |
| `created_at` | timestamp without time zone | no | now() |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `order_id` → `heladeria.customer_orders(id)` · ON DELETE CASCADE

**Índices:**
- `ix_tenant_order_cancel_logs_order_id`: CREATE INDEX ix_tenant_order_cancel_logs_order_id ON heladeria.order_cancel_logs USING btree (order_id)

#### `order_item_void_logs`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `order_item_id` | uuid | no |  |
| `motivo` | character varying(500) | no |  |
| `user_id` | uuid | no |  |
| `user_name` | character varying(255) | sí |  |
| `created_at` | timestamp without time zone | no | now() |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `order_item_id` → `heladeria.order_items(id)` · ON DELETE CASCADE

**Índices:**
- `ix_tenant_order_item_void_logs_order_item_id`: CREATE INDEX ix_tenant_order_item_void_logs_order_item_id ON heladeria.order_item_void_logs USING btree (order_item_id)

### Caja

#### `cash_registers`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `name` | character varying(255) | no |  |
| `active` | boolean | no |  |
| `id` 🔑 | uuid | no |  |

**Únicos:** `name`

#### `cash_shifts`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `cash_register_id` | uuid | no |  |
| `user_id` | uuid | no |  |
| `user_name` | character varying(255) | sí |  |
| `opening_amount` | numeric(12,2) | no | '0' |
| `opened_at` | timestamp without time zone | no | now() |
| `closed_at` | timestamp without time zone | sí |  |
| `counted_amount` | numeric(12,2) | sí |  |
| `status` | character varying(10) | no | 'open' |
| `id` 🔑 | uuid | no |  |
| `close_note` | character varying(500) | sí |  |

**Relaciones (FK):**
- `cash_register_id` → `heladeria.cash_registers(id)`

**Checks:**
- `ck__cash_shifts__ck_cash_shift_closed_has_timestamp`: CHECK ((((status)::text = 'open'::text) OR (closed_at IS NOT NULL)))
- `ck__cash_shifts__ck_cash_shift_opening_positive`: CHECK ((opening_amount >= (0)::numeric))
- `ck__cash_shifts__ck_cash_shift_status`: CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'closed'::character varying])::text[])))

**Índices:**
- `idx_open_shift_per_register`: CREATE UNIQUE INDEX idx_open_shift_per_register ON heladeria.cash_shifts USING btree (cash_register_id) WHERE ((status)::text = 'open'::text)
- `ix_tenant_cash_shifts_cash_register_id`: CREATE INDEX ix_tenant_cash_shifts_cash_register_id ON heladeria.cash_shifts USING btree (cash_register_id)

#### `cash_movements`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `cash_shift_id` | uuid | no |  |
| `kind` | character varying(20) | no |  |
| `amount` | numeric(12,2) | no |  |
| `description` | character varying(255) | sí |  |
| `user_id` | uuid | sí |  |
| `occurred_at` | timestamp without time zone | no | now() |
| `id` 🔑 | uuid | no |  |
| `category` | character varying(100) | sí |  |
| `user_name` | character varying(255) | sí |  |

**Relaciones (FK):**
- `cash_shift_id` → `heladeria.cash_shifts(id)` · ON DELETE CASCADE

**Checks:**
- `ck__cash_movements__ck_cash_movement_amount_positive`: CHECK ((amount > (0)::numeric))
- `ck__cash_movements__ck_cash_movement_kind`: CHECK (((kind)::text = ANY ((ARRAY['ingreso'::character varying, 'egreso'::character varying, 'retiro'::character varying])::text[])))

**Índices:**
- `ix_tenant_cash_movements_cash_shift_id`: CREATE INDEX ix_tenant_cash_movements_cash_shift_id ON heladeria.cash_movements USING btree (cash_shift_id)

#### `cash_count_denominations`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `cash_shift_id` | uuid | no |  |
| `denomination` | numeric(12,2) | no |  |
| `quantity` | integer | no |  |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `cash_shift_id` → `heladeria.cash_shifts(id)` · ON DELETE CASCADE

**Únicos:** `cash_shift_id,denomination`

**Checks:**
- `ck__cash_count_denominations__ck_cash_count_denominatio_33a3`: CHECK ((denomination > (0)::numeric))
- `ck__cash_count_denominations__ck_cash_count_quantity_positive`: CHECK ((quantity >= 0))

**Índices:**
- `ix_tenant_cash_count_denominations_cash_shift_id`: CREATE INDEX ix_tenant_cash_count_denominations_cash_shift_id ON heladeria.cash_count_denominations USING btree (cash_shift_id)

### Ventas

#### `sales`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `dining_session_id` | uuid | sí |  |
| `dining_table_id` | uuid | sí |  |
| `customer_order_id` | uuid | sí |  |
| `cash_shift_id` | uuid | no |  |
| `user_id` | uuid | no |  |
| `user_name` | character varying(255) | sí |  |
| `customer_name` | character varying(255) | sí |  |
| `subtotal` | numeric(12,2) | no | '0' |
| `discount` | numeric(12,2) | no | '0' |
| `tax` | numeric(12,2) | no | '0' |
| `tip` | numeric(12,2) | no | '0' |
| `total` | numeric(12,2) | no | '0' |
| `status` | character varying(10) | no | 'issued' |
| `sold_at` | timestamp without time zone | no | now() |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `cash_shift_id` → `heladeria.cash_shifts(id)`
- `customer_order_id` → `heladeria.customer_orders(id)`
- `dining_session_id` → `heladeria.dining_sessions(id)`
- `dining_table_id` → `heladeria.dining_tables(id)`

**Checks:**
- `ck__sales__ck_sale_status`: CHECK (((status)::text = ANY ((ARRAY['issued'::character varying, 'paid'::character varying, 'void'::character varying])::text[])))

**Índices:**
- `ix_tenant_sales_cash_shift_id`: CREATE INDEX ix_tenant_sales_cash_shift_id ON heladeria.sales USING btree (cash_shift_id)
- `ix_tenant_sales_customer_order_id`: CREATE INDEX ix_tenant_sales_customer_order_id ON heladeria.sales USING btree (customer_order_id)

#### `sale_items`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `sale_id` | uuid | no |  |
| `product_variant_id` | uuid | no |  |
| `description` | character varying(500) | no |  |
| `options` | jsonb | no | '[]' |
| `quantity` | integer | no |  |
| `unit_price` | numeric(12,2) | no |  |
| `line_total` | numeric(12,2) | no |  |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `product_variant_id` → `heladeria.product_variants(id)`
- `sale_id` → `heladeria.sales(id)` · ON DELETE CASCADE

**Checks:**
- `ck__sale_items__ck_sale_item_quantity_positive`: CHECK ((quantity > 0))

**Índices:**
- `ix_tenant_sale_items_sale_id`: CREATE INDEX ix_tenant_sale_items_sale_id ON heladeria.sale_items USING btree (sale_id)

#### `payment_methods`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `name` | character varying(100) | no |  |
| `is_cash` | boolean | no | false |
| `active` | boolean | no |  |
| `id` 🔑 | uuid | no |  |
| `type` | character varying(20) | no | 'other' |

**Únicos:** `name`

**Checks:**
- `ck__payment_methods__ck_payment_method_type`: CHECK (((type)::text = ANY ((ARRAY['cash'::character varying, 'card'::character varying, 'transfer'::character varying, 'other'::character varying])::text[])))

#### `payments`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `sale_id` | uuid | no |  |
| `payment_method_id` | uuid | no |  |
| `amount` | numeric(12,2) | no |  |
| `reference` | character varying(255) | sí |  |
| `paid_at` | timestamp without time zone | no | now() |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `payment_method_id` → `heladeria.payment_methods(id)`
- `sale_id` → `heladeria.sales(id)` · ON DELETE CASCADE

**Checks:**
- `ck__payments__ck_payment_amount_positive`: CHECK ((amount > (0)::numeric))

**Índices:**
- `ix_tenant_payments_payment_method_id`: CREATE INDEX ix_tenant_payments_payment_method_id ON heladeria.payments USING btree (payment_method_id)
- `ix_tenant_payments_sale_id`: CREATE INDEX ix_tenant_payments_sale_id ON heladeria.payments USING btree (sale_id)

### Facturación

#### `invoices`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `sale_id` | uuid | no |  |
| `customer_order_id` | uuid | sí |  |
| `prefix` | character varying(20) | no | '' |
| `number` | integer | no |  |
| `customer_name` | character varying(255) | sí |  |
| `subtotal` | numeric(12,2) | no | '0' |
| `discount` | numeric(12,2) | no | '0' |
| `tax` | numeric(12,2) | no | '0' |
| `tip` | numeric(12,2) | no | '0' |
| `total` | numeric(12,2) | no | '0' |
| `status` | character varying(10) | no | 'issued' |
| `issued_at` | timestamp without time zone | no | now() |
| `user_id` | uuid | no |  |
| `user_name` | character varying(255) | sí |  |
| `cufe` | character varying(255) | sí |  |
| `dian_status` | character varying(20) | sí |  |
| `dian_sent_at` | timestamp without time zone | sí |  |
| `id` 🔑 | uuid | no |  |

**Relaciones (FK):**
- `customer_order_id` → `heladeria.customer_orders(id)`
- `sale_id` → `heladeria.sales(id)`

**Únicos:** `prefix,number`

**Checks:**
- `ck__invoices__ck_invoice_status`: CHECK (((status)::text = ANY ((ARRAY['issued'::character varying, 'void'::character varying])::text[])))

**Índices:**
- `ix_tenant_invoices_customer_order_id`: CREATE INDEX ix_tenant_invoices_customer_order_id ON heladeria.invoices USING btree (customer_order_id)
- `ix_tenant_invoices_sale_id`: CREATE UNIQUE INDEX ix_tenant_invoices_sale_id ON heladeria.invoices USING btree (sale_id)

#### `invoice_counters`

| Columna | Tipo | Null | Default |
|---|---|---|---|
| `prefix` | character varying(20) | no | '' |
| `next_number` | integer | no | 1 |
| `id` 🔑 | uuid | no |  |

**Únicos:** `prefix`
