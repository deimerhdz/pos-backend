# Golden master — motor de precio y consumo de inventario

## Qué es

`pricing_consumption.master.json` es una fotografía congelada del
comportamiento **actual** del núcleo de cálculo del flujo de pedido de mesa
por QR (identificado en `specs/000-reconocimiento/flujo-pedido-qr.md` del
repositorio `pos-specs`, secciones 2 a 4 y 10): por cada línea de pedido,
`app/api/v1/catalog/line_pricing.py` (precio de línea + validación de
opciones) y `app/api/v1/catalog/consumption_plan.py` (qué se descuenta de
inventario), encadenadas exactamente como las encadena la aplicación real en
`cart.service.add_item`, `orders.consolidation.add_item_to_table` y
`orders.service.create_order`.

Contiene 12 casos de entrada fijos (`app/characterization_tests/golden_master_core.py`,
lista `CASES`) que entre todos ejercitan las reglas de negocio (RN-CAT-01,
02, 17, 18, 20-25, 28, 30, 34) y las anomalías (A-02 [PROTEGIDA], A-06,
RN-CAT-32/35) más importantes del motor — ver el enunciado exacto de cada
regla/anomalía en `specs/000-reconocimiento/reglas-de-negocio.md` y
`registro-de-anomalias.md`.

Cada entrada guarda el precio de línea, el resultado de la validación de
opciones, el plan de consumo completo (insumo, cantidad con todos los
decimales, origen) y/o el resultado de `ensure_lines_consume_inventory` /
`check_availability`, según lo que el caso ejercite. Los insumos, variantes
y grupos se referencian **por nombre**, nunca por UUID: los IDs son
aleatorios en cada ejecución del generador (SQLite en memoria, base nueva
cada vez), así que el JSON solo es estable si nunca se serializa un UUID
crudo. El archivo tiene claves ordenadas (`sort_keys=True`) e indentación
fija para que un `diff` sea legible campo a campo.

## Cómo se verifica

`test_golden_master_pricing_consumption.py` reconstruye el reporte con
`golden_master_core.build_report()` y lo compara campo a campo contra este
archivo. Si algo cambió, el mensaje de fallo señala exactamente el caso y el
campo que divergió (no solo "el JSON no matchea").

## Cuándo se regenera

**Nunca de forma automática, y nunca solo porque el test falla.** Un fallo
de este test significa que el comportamiento del motor de precio/consumo
cambió — la Constitución del repositorio `pos-specs` (Principio I: "el
comportamiento actual es sagrado") exige que ese cambio esté **autorizado
por una decisión de negocio explícita y por escrito** antes de aceptarlo
como el nuevo comportamiento correcto.

Antes de regenerar:

1. Confirmar que el cambio de código que causó la divergencia fue una
   decisión de negocio documentada (no un efecto secundario accidental de
   otro cambio).
2. Revisar el diff completo entre el master viejo y el nuevo reporte —no
   solo el caso que falló el test— para asegurarse de que no cambió nada
   más de lo esperado.
3. Si el cambio afecta una entrada `[PROTEGIDA]` (hoy, el caso `03` — A-02)
   o alguna clasificada `DUDOSA`/`PENDIENTE` en `registro-de-anomalias.md`,
   la regeneración requiere que la decisión de negocio quede registrada
   primero en ese documento (actualizar la fila de la tabla de la entrevista
   o añadir una nueva), no solo en el mensaje del commit de código.

Cómo regenerar, una vez autorizado:

```bash
cd pos-backend
source env/bin/activate
python3 -c "
from app.characterization_tests.golden_master_core import build_report, report_to_json
open('app/characterization_tests/golden_master/pricing_consumption.master.json', 'w').write(
    report_to_json(build_report())
)
"
python3 -m unittest app.characterization_tests.test_golden_master_pricing_consumption -v
```

Y commitear el nuevo `pricing_consumption.master.json` junto con la
referencia a la decisión de negocio que lo autorizó (en el mensaje de commit
y/o en `registro-de-anomalias.md`).
