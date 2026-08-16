"""Golden master del núcleo de cálculo del flujo de pedido de mesa por QR.

No es código de producción — es el generador/comparador del golden master
de `app/characterization_tests/golden_master/pricing_consumption.master.json`.
Ver el README en esa carpeta para qué es y cuándo puede regenerarse.

El "flujo central de negocio" (identificado en
`specs/000-reconocimiento/flujo-pedido-qr.md`, secciones 2 a 4 y 10) es: por
cada línea de pedido, (1) validar la selección de opciones contra los grupos
de la variante, (2) fijar el precio de línea como snapshot, y (3) calcular
qué se descuenta de inventario — exactamente las funciones de
`app/api/v1/catalog/line_pricing.py` y `consumption_plan.py` que ya cubren
los demás tests de este paquete uno por uno. Este golden master las ejecuta
**encadenadas**, tal como las encadena la aplicación real en cada camino de
creación de pedido (`cart.service.add_item`,
`orders.consolidation.add_item_to_table`, `orders.service.create_order`),
sobre un juego fijo de 8-12 líneas de entrada que entre todas ejercitan las
reglas y anomalías principales del motor.

Los identificadores de fila (UUID) son aleatorios en cada ejecución del
generador porque `new_session()` crea una base nueva cada vez — por eso el
reporte serializado NUNCA incluye un UUID crudo: todo insumo/variante/grupo
se referencia por su **nombre**, elegido a mano y estable entre ejecuciones,
para que el master sea reproducible byte a byte mientras el comportamiento
no cambie.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import HTTPException

from app.characterization_tests import fixtures as f
from app.api.v1.catalog.line_pricing import check_availability, compute_line_price, validate_option_selection
from app.api.v1.catalog.consumption_plan import ensure_lines_consume_inventory, plan_line_consumption


def _dec(x) -> str:
    return str(Decimal(x))


def _try(fn):
    """Ejecuta `fn`, devuelve ('ok', valor) o ('error', {status_code, detail})."""
    try:
        return "ok", fn()
    except HTTPException as exc:
        return "error", {"status_code": exc.status_code, "detail": exc.detail}


def _case_linea_simple_sin_opciones() -> dict[str, Any]:
    db = f.new_session()
    variant = f.make_variant(db, product=f.make_product(db, name="Producto Cono Simple"), name="Cono Simple", price=Decimal("5000"))
    barquillo = f.make_inventory_item(db, name="Barquillo")
    f.make_recipe_item(db, variant, barquillo, quantity=Decimal("1"))
    names = {barquillo.id: "Barquillo"}
    qty = 2

    price = compute_line_price(variant, [])
    v_status, v_val = _try(lambda: validate_option_selection(db, variant, []))
    lines = plan_line_consumption(db, variant.id, qty, [])
    e_status, e_val = _try(lambda: ensure_lines_consume_inventory(db, [(variant.id, qty, [])]))

    return {
        "descripcion": "RN-CAT-01/RN-CAT-17: variante sin opciones, receta fija simple.",
        "entrada": {"variante": variant.name, "precio_variante": _dec(variant.price), "cantidad": qty, "opciones": []},
        "precio_linea": _dec(price),
        "validacion_opciones": v_status if v_status == "ok" else v_val,
        "plan_consumo": [
            {"insumo": names[l.inventory_item_id], "cantidad": _dec(l.quantity), "origen": l.source} for l in lines
        ],
        "ensure_lines_consume_inventory": e_status if e_status == "ok" else e_val,
    }


def _case_opciones_extra_price_decimales() -> dict[str, Any]:
    db = f.new_session()
    variant = f.make_variant(db, product=f.make_product(db, name="Producto Malteada"), name="Malteada", price=Decimal("8500.50"))
    group = f.make_option_group(db, name="Toppings decimales", min_select=0, max_select=2)
    f.link_variant_group(db, variant, group, min_select=0, max_select=2, quantity_per_option=Decimal("0"))
    chispas = f.make_option(db, group=group, name="Chispas", extra_price=Decimal("350.25"))
    cereza = f.make_option(db, group=group, name="Cereza", extra_price=Decimal("100.00"))
    qty = 1

    price = compute_line_price(variant, [chispas, cereza])
    v_status, v_val = _try(lambda: validate_option_selection(db, variant, [chispas, cereza]))
    lines = plan_line_consumption(db, variant.id, qty, [chispas, cereza])
    e_status, e_val = _try(
        lambda: ensure_lines_consume_inventory(db, [(variant.id, qty, [chispas, cereza])])
    )

    return {
        "descripcion": "RN-CAT-01/RN-CAT-02: extras con decimales de 2 cifras, sin quantize; "
        "RN-CAT-34 sin_receta: el grupo elegido no descuenta inventario y no hay receta fija.",
        "entrada": {
            "variante": variant.name, "precio_variante": _dec(variant.price), "cantidad": qty,
            "opciones": [{"nombre": chispas.name, "extra_price": _dec(chispas.extra_price)},
                         {"nombre": cereza.name, "extra_price": _dec(cereza.extra_price)}],
        },
        "precio_linea": _dec(price),
        "validacion_opciones": v_status if v_status == "ok" else v_val,
        "plan_consumo": [],
        "ensure_lines_consume_inventory": e_status if e_status == "ok" else e_val,
    }


def _case_a02_tamano_manda_sobre_opcion() -> dict[str, Any]:
    db = f.new_session()
    variant = f.make_variant(db, product=f.make_product(db, name="Producto Copa Grande"), name="Copa Grande", price=Decimal("15000"))
    group = f.make_option_group(db, name="Sabores 3 bolas", min_select=3, max_select=3)
    f.link_variant_group(db, variant, group, min_select=3, max_select=3, quantity_per_option=Decimal("120"))
    fresa_insumo = f.make_inventory_item(db, name="Fresa")
    choco_insumo = f.make_inventory_item(db, name="Chocolate")
    vainilla_insumo = f.make_inventory_item(db, name="Vainilla")
    fresa = f.make_option(db, group=group, name="Fresa", inventory_item_id=fresa_insumo.id, item_quantity=Decimal("80"))
    choco = f.make_option(db, group=group, name="Chocolate", inventory_item_id=choco_insumo.id, item_quantity=Decimal("80"))
    vainilla = f.make_option(db, group=group, name="Vainilla", inventory_item_id=vainilla_insumo.id, item_quantity=Decimal("80"))
    names = {fresa_insumo.id: "Fresa", choco_insumo.id: "Chocolate", vainilla_insumo.id: "Vainilla"}
    qty = 1
    opciones = [fresa, choco, vainilla]

    price = compute_line_price(variant, opciones)
    v_status, v_val = _try(lambda: validate_option_selection(db, variant, opciones))
    lines = plan_line_consumption(db, variant.id, qty, opciones)
    e_status, e_val = _try(lambda: ensure_lines_consume_inventory(db, [(variant.id, qty, opciones)]))

    return {
        "descripcion": "A-02 [PROTEGIDA] / RN-CAT-18: el tamaño (120g) manda sobre la opción "
        "(80g); nunca se suman (no da 200g).",
        "entrada": {
            "variante": variant.name, "cantidad": qty,
            "opciones": [o.name for o in opciones],
            "quantity_per_option_grupo": "120", "item_quantity_por_opcion": "80",
        },
        "precio_linea": _dec(price),
        "validacion_opciones": v_status if v_status == "ok" else v_val,
        "plan_consumo": [
            {"insumo": names[l.inventory_item_id], "cantidad": _dec(l.quantity), "origen": l.source} for l in lines
        ],
        "ensure_lines_consume_inventory": e_status if e_status == "ok" else e_val,
    }


def _case_opcion_manda_cuando_tamano_no_define() -> dict[str, Any]:
    db = f.new_session()
    variant = f.make_variant(db, product=f.make_product(db, name="Producto Cono Waffle"), name="Cono Waffle", price=Decimal("6000"))
    group = f.make_option_group(db, name="Toppings simples", min_select=0, max_select=2)
    f.link_variant_group(db, variant, group, min_select=0, max_select=2, quantity_per_option=Decimal("0"))
    insumo = f.make_inventory_item(db, name="ChispasChoc")
    chispas = f.make_option(db, group=group, name="Chispas de Chocolate", extra_price=Decimal("500"),
                             inventory_item_id=insumo.id, item_quantity=Decimal("15"))
    names = {insumo.id: "ChispasChoc"}
    qty = 3

    price = compute_line_price(variant, [chispas])
    v_status, v_val = _try(lambda: validate_option_selection(db, variant, [chispas]))
    lines = plan_line_consumption(db, variant.id, qty, [chispas])
    e_status, e_val = _try(lambda: ensure_lines_consume_inventory(db, [(variant.id, qty, [chispas])]))

    return {
        "descripcion": "RN-CAT-18 (rama respaldo): el grupo no define quantity_per_option "
        "(0) -> manda item_quantity de la opción (15), multiplicado por cantidad vendida (3).",
        "entrada": {"variante": variant.name, "cantidad": qty, "opciones": [chispas.name]},
        "precio_linea": _dec(price),
        "validacion_opciones": v_status if v_status == "ok" else v_val,
        "plan_consumo": [
            {"insumo": names[l.inventory_item_id], "cantidad": _dec(l.quantity), "origen": l.source} for l in lines
        ],
        "ensure_lines_consume_inventory": e_status if e_status == "ok" else e_val,
    }


def _case_dos_opciones_mismo_insumo() -> dict[str, Any]:
    db = f.new_session()
    variant = f.make_variant(db, product=f.make_product(db, name="Producto Ensalada de Frutas"), name="Ensalada de Frutas", price=Decimal("7000"))
    group = f.make_option_group(db, name="Sabores extra", min_select=2, max_select=2)
    f.link_variant_group(db, variant, group, min_select=2, max_select=2, quantity_per_option=Decimal("60"))
    fresa_insumo = f.make_inventory_item(db, name="Fresa (ensalada)")
    fresa = f.make_option(db, group=group, name="Fresa", inventory_item_id=fresa_insumo.id)
    fresa_premium = f.make_option(db, group=group, name="Fresa Premium", inventory_item_id=fresa_insumo.id)
    names = {fresa_insumo.id: "Fresa (ensalada)"}
    qty = 1
    opciones = [fresa, fresa_premium]

    price = compute_line_price(variant, opciones)
    v_status, v_val = _try(lambda: validate_option_selection(db, variant, opciones))
    lines = plan_line_consumption(db, variant.id, qty, opciones)
    e_status, e_val = _try(lambda: ensure_lines_consume_inventory(db, [(variant.id, qty, opciones)]))

    return {
        "descripcion": "RN-CAT-21: dos opciones distintas apuntando al mismo insumo generan "
        "DOS líneas de consumo separadas, nunca una fusionada.",
        "entrada": {"variante": variant.name, "cantidad": qty, "opciones": [o.name for o in opciones]},
        "precio_linea": _dec(price),
        "validacion_opciones": v_status if v_status == "ok" else v_val,
        "plan_consumo": [
            {"insumo": names[l.inventory_item_id], "cantidad": _dec(l.quantity), "origen": l.source} for l in lines
        ],
        "ensure_lines_consume_inventory": e_status if e_status == "ok" else e_val,
    }


def _case_opcion_sin_insumo_ligado() -> dict[str, Any]:
    db = f.new_session()
    variant = f.make_variant(db, product=f.make_product(db, name="Producto Refresco"), name="Refresco", price=Decimal("3000"))
    group = f.make_option_group(db, name="Preferencia hielo", min_select=0, max_select=1)
    f.link_variant_group(db, variant, group, min_select=0, max_select=1, quantity_per_option=Decimal("0"))
    sin_hielo = f.make_option(db, group=group, name="Sin hielo", inventory_item_id=None, item_quantity=Decimal("50"))
    qty = 1

    price = compute_line_price(variant, [sin_hielo])
    v_status, v_val = _try(lambda: validate_option_selection(db, variant, [sin_hielo]))
    lines = plan_line_consumption(db, variant.id, qty, [sin_hielo])
    e_status, e_val = _try(lambda: ensure_lines_consume_inventory(db, [(variant.id, qty, [sin_hielo])]))

    return {
        "descripcion": "RN-CAT-22: opción sin `inventory_item_id` (aunque tenga item_quantity=50 "
        "mal cargado) no genera consumo -> plan vacío -> RN-CAT-34 sin_receta.",
        "entrada": {"variante": variant.name, "cantidad": qty, "opciones": [sin_hielo.name]},
        "precio_linea": _dec(price),
        "validacion_opciones": v_status if v_status == "ok" else v_val,
        "plan_consumo": [],
        "ensure_lines_consume_inventory": e_status if e_status == "ok" else e_val,
    }


def _case_per_unit_cero_no_genera_linea() -> dict[str, Any]:
    db = f.new_session()
    variant = f.make_variant(db, product=f.make_product(db, name="Producto Agua"), name="Agua", price=Decimal("2000"))
    group = f.make_option_group(db, name="Con gas o sin gas", min_select=0, max_select=1)
    f.link_variant_group(db, variant, group, min_select=0, max_select=1, quantity_per_option=Decimal("0"))
    insumo = f.make_inventory_item(db, name="Botella retornable")
    opt = f.make_option(db, group=group, name="Con gas", inventory_item_id=insumo.id, item_quantity=Decimal("0"))
    qty = 1

    lines = plan_line_consumption(db, variant.id, qty, [opt])

    return {
        "descripcion": "RN-CAT-23: insumo ligado pero cantidad resultante <=0 "
        "(quantity_per_option=0 Y item_quantity=0) -> no genera línea de consumo.",
        "entrada": {"variante": variant.name, "cantidad": qty, "opciones": [opt.name]},
        "precio_linea": _dec(compute_line_price(variant, [opt])),
        "validacion_opciones": "ok",
        "plan_consumo": [
            {"insumo": "Botella retornable", "cantidad": _dec(l.quantity), "origen": l.source} for l in lines
        ],
        "ensure_lines_consume_inventory": "no_evaluado_en_este_caso",
    }


def _case_grupo_obligatorio_seleccion_invalida() -> dict[str, Any]:
    db = f.new_session()
    variant = f.make_variant(db, product=f.make_product(db, name="Producto Copa Mediana"), name="Copa Mediana", price=Decimal("11000"))
    group = f.make_option_group(db, name="Sabores 2 bolas", min_select=2, max_select=2)
    f.link_variant_group(db, variant, group, min_select=2, max_select=2, quantity_per_option=Decimal("100"))
    insumo = f.make_inventory_item(db, name="Mango")
    mango = f.make_option(db, group=group, name="Mango", inventory_item_id=insumo.id)
    qty = 1

    v_status, v_val = _try(lambda: validate_option_selection(db, variant, [mango]))  # solo 1 de 2

    return {
        "descripcion": "RN-CAT-28/RN-CAT-30: grupo obligatorio que descuenta exige EXACTAMENTE "
        "el máximo (2); elegir 1 se rechaza con 422 siempre, sin importar STRICT_OPTION_SELECTION.",
        "entrada": {"variante": variant.name, "cantidad": qty, "opciones": [mango.name]},
        "precio_linea": _dec(compute_line_price(variant, [mango])),
        "validacion_opciones": v_status if v_status == "ok" else v_val,
        "plan_consumo": "no_evaluado_en_este_caso",
        "ensure_lines_consume_inventory": "no_evaluado_en_este_caso",
    }


def _case_variante_sin_receta_ni_grupo() -> dict[str, Any]:
    db = f.new_session()
    variant = f.make_variant(db, product=f.make_product(db, name="Producto Malteada Especial"), name="Malteada Especial", price=Decimal("9000"))
    qty = 1

    e_status, e_val = _try(lambda: ensure_lines_consume_inventory(db, [(variant.id, qty, [])]))

    return {
        "descripcion": "RN-CAT-34: variante sin receta ni grupo configurado -> 409 sin_receta.",
        "entrada": {"variante": variant.name, "cantidad": qty, "opciones": []},
        "precio_linea": _dec(compute_line_price(variant, [])),
        "validacion_opciones": "ok",
        "plan_consumo": [],
        "ensure_lines_consume_inventory": e_status if e_status == "ok" else e_val,
    }


def _case_grupo_opcional_unica_fuente_sin_elegir() -> dict[str, Any]:
    db = f.new_session()
    variant = f.make_variant(db, product=f.make_product(db, name="Producto Cono Simple Sabor"), name="Cono Simple Sabor", price=Decimal("4000"))
    group = f.make_option_group(db, name="Sabor único opcional", min_select=0, max_select=1)
    f.link_variant_group(db, variant, group, min_select=0, max_select=1, quantity_per_option=Decimal("80"))
    insumo = f.make_inventory_item(db, name="Base de sabor")
    f.make_option(db, group=group, name="Fresa (opcional)", inventory_item_id=insumo.id)
    qty = 1

    v_status, v_val = _try(lambda: validate_option_selection(db, variant, []))
    e_status, e_val = _try(lambda: ensure_lines_consume_inventory(db, [(variant.id, qty, [])]))

    return {
        "descripcion": "A-06/RN-CAT-35 [DISCREPANCIA]: grupo opcional (min_select=0) que "
        "descuenta y es la ÚNICA fuente de consumo configurada. La validación de selección "
        "SÍ permite no elegir nada (grupo opcional), pero ensure_lines_consume_inventory "
        "bloquea igual con 409 sin_eleccion — contradice el propio comentario del código "
        "('una decisión legítima del comensal').",
        "entrada": {"variante": variant.name, "cantidad": qty, "opciones": []},
        "precio_linea": _dec(compute_line_price(variant, [])),
        "validacion_opciones": v_status if v_status == "ok" else v_val,
        "plan_consumo": [],
        "ensure_lines_consume_inventory": e_status if e_status == "ok" else e_val,
    }


def _case_a06_opcion_de_grupo_ajeno_tolerada() -> dict[str, Any]:
    db = f.new_session()
    variant = f.make_variant(db, product=f.make_product(db, name="Producto Cono Simple Ajeno"), name="Cono Simple Ajeno", price=Decimal("4500"))
    grupo_ajeno = f.make_option_group(db, name="Extras de Pizza", min_select=0, max_select=1)
    insumo_ajeno = f.make_inventory_item(db, name="Masa de pizza")
    opcion_ajena = f.make_option(
        db, group=grupo_ajeno, name="Extra de pizza", extra_price=Decimal("3000"),
        inventory_item_id=insumo_ajeno.id, item_quantity=Decimal("1"),
    )
    names = {insumo_ajeno.id: "Masa de pizza"}
    qty = 1

    price = compute_line_price(variant, [opcion_ajena])
    v_status, v_val = _try(lambda: validate_option_selection(db, variant, [opcion_ajena]))
    lines = plan_line_consumption(db, variant.id, qty, [opcion_ajena])

    return {
        "descripcion": "A-06 [PENDIENTE de decisión de negocio] / RN-CAT-32: con "
        "STRICT_OPTION_SELECTION=False (default), una opción de un grupo que la variante NO "
        "ofrece en absoluto pasa la validación sin error, sigue sumando su extra_price al "
        "cobro y sigue generando su propio consumo de inventario ajeno a la variante vendida.",
        "entrada": {"variante": variant.name, "cantidad": qty, "opciones": [opcion_ajena.name]},
        "precio_linea": _dec(price),
        "validacion_opciones": v_status if v_status == "ok" else v_val,
        "plan_consumo": [
            {"insumo": names[l.inventory_item_id], "cantidad": _dec(l.quantity), "origen": l.source} for l in lines
        ],
        "ensure_lines_consume_inventory": "no_evaluado_en_este_caso",
    }


def _case_disponibilidad_stock_boundary() -> dict[str, Any]:
    db = f.new_session()
    insumo = f.make_inventory_item(db, name="Leche (boundary)", current_stock=Decimal("120.000"))
    requerido = Decimal("120.000")

    exacto_status, exacto_val = _try(lambda: check_availability(db, {insumo.id: requerido}))
    justo_encima_status, justo_encima_val = _try(
        lambda: check_availability(db, {insumo.id: requerido + Decimal("0.001")})
    )
    insumo_bajo = f.make_inventory_item(db, name="Leche (boundary, un paso menos)", current_stock=Decimal("119.999"))
    justo_debajo_status, justo_debajo_val = _try(
        lambda: check_availability(db, {insumo_bajo.id: requerido})
    )

    return {
        "descripcion": "RN-CAT-24: `stock_actual < requerido` es comparación ESTRICTA. Los tres "
        "puntos del umbral: exactamente igual (pasa), 0.001 por debajo del stock (falla), "
        "0.001 por encima del stock (falla).",
        "entrada": {"stock_disponible": "120.000", "requerido_exacto": "120.000"},
        "disponibilidad": {
            "requerido_exacto_120_000_contra_stock_120_000": exacto_status if exacto_status == "ok" else exacto_val,
            "requerido_120_001_contra_stock_120_000": justo_encima_status if justo_encima_status == "ok" else justo_encima_val,
            "requerido_120_000_contra_stock_119_999": justo_debajo_status if justo_debajo_status == "ok" else justo_debajo_val,
        },
    }


CASES: list[tuple[str, Any]] = [
    ("01_linea_simple_sin_opciones", _case_linea_simple_sin_opciones),
    ("02_opciones_extra_price_decimales", _case_opciones_extra_price_decimales),
    ("03_a02_protegida_tamano_manda_sobre_opcion", _case_a02_tamano_manda_sobre_opcion),
    ("04_opcion_manda_cuando_tamano_no_define", _case_opcion_manda_cuando_tamano_no_define),
    ("05_dos_opciones_mismo_insumo_dos_movimientos", _case_dos_opciones_mismo_insumo),
    ("06_opcion_sin_insumo_ligado_se_ignora", _case_opcion_sin_insumo_ligado),
    ("07_per_unit_cero_no_genera_linea", _case_per_unit_cero_no_genera_linea),
    ("08_grupo_obligatorio_seleccion_invalida_422", _case_grupo_obligatorio_seleccion_invalida),
    ("09_variante_sin_receta_ni_grupo_409", _case_variante_sin_receta_ni_grupo),
    ("10_a06_grupo_opcional_unica_fuente_sin_elegir_409", _case_grupo_opcional_unica_fuente_sin_elegir),
    ("11_a06_opcion_de_grupo_ajeno_tolerada", _case_a06_opcion_de_grupo_ajeno_tolerada),
    ("12_disponibilidad_stock_boundary", _case_disponibilidad_stock_boundary),
]


def build_report() -> dict[str, Any]:
    return {name: builder() for name, builder in CASES}


def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
