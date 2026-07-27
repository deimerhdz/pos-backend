# Requerimientos Funcionales - Sistema POS para Heladerías, Tiendas de Granizados y Restaurantes

## 1. Gestión del Catálogo

### RF-001. Gestión de productos

El sistema debe permitir crear, editar, consultar, activar e inactivar productos del menú.

### RF-002. Categorías

El sistema debe permitir organizar los productos por categorías.

**Ejemplos:**

- Helados
- Granizados
- Bebidas
- Postres
- Snacks
- Adiciones

### RF-003. Variantes

El sistema debe permitir configurar variantes para cada producto.

**Ejemplos:**

- Tamaño
- Sabor
- Presentación
- Tipo de vaso
- Tipo de leche

### RF-004. Toppings

El sistema debe permitir configurar toppings o ingredientes adicionales con un costo independiente.

**Ejemplos:**

- Chispas de chocolate
- Queso
- Crema
- Galleta triturada
- Frutas

### RF-005. Modificadores

El sistema debe permitir configurar modificadores sin costo adicional.

**Ejemplos:**

- Sin azúcar
- Sin crema
- Poco hielo
- Extra hielo

### RF-006. Disponibilidad

El sistema debe permitir marcar productos como disponibles o agotados temporalmente.

### RF-007. Información del producto

El sistema debe permitir asociar imágenes, descripciones y precios a cada producto.

---

# 2. Promociones

### RF-008. Gestión de promociones

El sistema debe permitir crear, editar y eliminar promociones.

### RF-009. Tipos de promociones

El sistema debe permitir configurar diferentes tipos de promociones.

- Descuento porcentual
- Descuento por valor fijo
- Compra X y lleva Y
- Combos
- Precio especial por cantidad

### RF-010. Vigencia

El sistema debe permitir definir fechas y horarios de vigencia para una promoción.

### RF-011. Restricciones

El sistema debe permitir limitar promociones por días de la semana y horarios.

### RF-012. Aplicación automática

El sistema debe aplicar automáticamente las promociones durante la venta.

---

# 3. Inventario

### RF-013. Materias primas

El sistema debe permitir administrar materias primas e insumos.

### RF-014. Productos terminados

El sistema debe permitir administrar productos terminados.

### RF-015. Entradas

El sistema debe permitir registrar entradas de inventario provenientes de compras.

### RF-016. Salidas

El sistema debe permitir registrar salidas de inventario por:

- Venta
- Daño
- Vencimiento
- Consumo interno
- Ajustes

### RF-017. Historial

El sistema debe almacenar el historial completo de movimientos del inventario.

### RF-018. Ajustes

El sistema debe permitir realizar ajustes manuales del inventario.

### RF-019. Stock mínimo

El sistema debe generar alertas cuando un producto alcance el stock mínimo configurado.

### RF-020. Descuento automático

El sistema debe descontar automáticamente los insumos utilizados en una venta cuando exista una receta configurada.

---

# 4. Compras

### RF-021. Órdenes de compra

El sistema debe permitir crear órdenes de compra para proveedores.

### RF-022. Recepción

El sistema debe permitir registrar la recepción parcial o total de una orden de compra.

### RF-023. Actualización automática

El sistema debe actualizar automáticamente el inventario al recibir una compra.

---

# 5. Punto de Venta (POS)

### RF-024. Venta manual

El sistema debe permitir crear órdenes manualmente desde el módulo POS.

### RF-025. Búsqueda

El sistema debe permitir buscar productos por nombre o categoría.

### RF-026. Modificación

El sistema debe permitir modificar cantidades antes de finalizar la venta.

### RF-027. Cálculo automático

El sistema debe calcular automáticamente:

- Subtotal
- Impuestos
- Descuentos
- Total

### RF-028. Métodos de pago

El sistema debe permitir registrar múltiples métodos de pago.

- Efectivo
- Tarjeta
- Transferencia
- QR
- Pago combinado

### RF-029. Cambio

El sistema debe calcular automáticamente el cambio cuando el pago sea en efectivo.

### RF-030. Cancelación

El sistema debe permitir cancelar una venta antes de finalizarla.

### RF-031. Reimpresión

El sistema debe permitir reimprimir comprobantes de venta.

---

# 6. Pedidos mediante Código QR

### RF-032. QR por mesa

El sistema debe permitir generar un código QR único para cada mesa.

### RF-033. Cliente anónimo

El cliente deberá poder acceder al menú mediante el código QR sin necesidad de registrarse(solo ingresar su nombre).

### RF-034. Menú digital

El sistema deberá mostrar el menú actualizado del establecimiento.

### RF-035. Carrito

El cliente deberá poder agregar productos al carrito.

### RF-036. Variantes

Cuando un producto tenga variantes, el sistema deberá solicitar su selección antes de agregar el producto al carrito.

### RF-037. Toppings

El cliente deberá poder agregar toppings o ingredientes adicionales con costo extra.

### RF-038. Observaciones

El cliente deberá poder escribir observaciones para cada producto.

**Ejemplos:**

- Poco hielo
- Sin azúcar
- Extra chocolate

### RF-039. Confirmación

El cliente deberá poder confirmar el pedido desde su dispositivo móvil.

### RF-040. Recepción del pedido

El pedido deberá aparecer automáticamente en el panel del establecimiento.

### RF-041. Estados del pedido

El sistema deberá permitir administrar los siguientes estados:

- Pendiente
- Confirmado
- En preparación
- Listo
- Entregado
- Cancelado

---

# 7. Gestión de Caja

### RF-042. Apertura

El sistema debe permitir realizar la apertura de caja indicando:

- Cajero
- Fecha
- Fondo inicial

### RF-043. Registro automático

El sistema debe registrar automáticamente todas las ventas realizadas durante el turno.

### RF-044. Ingresos adicionales

El sistema debe permitir registrar ingresos diferentes a las ventas.

### RF-045. Retiros

El sistema debe permitir registrar retiros de efectivo.

**Ejemplos:**

- Compra de insumos
- Pago de domicilio
- Gastos menores

### RF-046. Arqueos

El sistema debe permitir realizar arqueos parciales durante el turno.

### RF-047. Cierre

El sistema debe permitir realizar el cierre de caja.

### RF-048. Diferencias

El sistema debe calcular automáticamente las diferencias entre el dinero esperado y el dinero contado.

### RF-049. Reporte de cierre

El sistema debe generar un resumen del cierre de caja.

---

# 8. Gestión de Mesas

### RF-050. Administración de mesas

El sistema debe permitir crear y administrar mesas.

### RF-051. Estado de las mesas

El sistema deberá mostrar el estado de cada mesa.

- Libre
- Ocupada
- Reservada
- Pendiente de pago

### RF-052. Cambio de mesa

El sistema debe permitir mover pedidos entre mesas.

### RF-053. Unión de mesas

El sistema debe permitir unir varias mesas en una sola orden.

### RF-054. División de cuenta

El sistema debe permitir dividir una cuenta entre varios clientes.

# 10. Reportes

### RF-059. Ventas

El sistema debe generar reportes de ventas.

### RF-060. Productos

El sistema debe generar reportes de ventas por producto.

### RF-061. Categorías

El sistema debe generar reportes por categoría.

### RF-062. Cajeros

El sistema debe generar reportes por cajero.

### RF-063. Inventario

El sistema debe generar reportes del inventario.

### RF-064. Productos más vendidos

El sistema debe generar reportes de productos más vendidos.

### RF-065. Rentabilidad

El sistema debe generar reportes de rentabilidad.

---

# 11. Administración

### RF-066. Multiempresa

El sistema debe soportar múltiples empresas (Multi-tenant).

### RF-068. Usuarios

El sistema debe permitir administrar usuarios.

### RF-069. Roles y permisos

El sistema debe permitir administrar roles y permisos.

### RF-071. Métodos de pago

El sistema debe permitir administrar métodos de pago.

### RF-072. cajas

El sistema debe permitir configurar caja.

### RF-073. Horarios

El sistema debe permitir configurar horarios de atención.

### RF-074. Recetas

El sistema debe permitir configurar recetas para descontar automáticamente el inventario.

### RF-075. Proveedores

El sistema debe permitir administrar proveedores.

### RF-076. Auditoría

El sistema debe registrar un historial de auditoría sobre las operaciones críticas del sistema.

---

# 12. Funcionalidades recomendadas para futuras versiones

Estas funcionalidades no forman parte del MVP, pero se recomienda diseñar la arquitectura pensando en soportarlas.

- Programa de fidelización de clientes.
- Cupones de descuento.
- Menú digital personalizable.
- Integración con WhatsApp.
- Integración con Rappi, Uber Eats y plataformas de domicilios.
- Notificaciones Push.
- Dashboard gerencial en tiempo real.
- Reportes financieros avanzados.
- Facturación electrónica.
- Integración con DIAN.
- Integración con balanzas electrónicas.
- Gestión de domicilios propios.
- Aplicación móvil para administradores.
- Aplicación móvil para meseros.
- Modo Offline para el POS.
- Sincronización automática cuando regrese la conexión.
- Inteligencia Artificial para pronóstico de ventas.
- Inteligencia Artificial para sugerencia de compras.
- Inteligencia Artificial para recomendaciones de promociones.
- API pública para integraciones con terceros.
