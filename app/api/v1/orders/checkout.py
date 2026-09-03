"""Cobro y ciclo de cierre de una orden de mesa (Fase 7): bloqueo con lock
optimista + validación de cocina, cuenta/split por comensal, pago (crea Sale sin
re-descontar), cancelación con reversa **parcial**, y liberación de mesa.

La reversa de inventario al cancelar es asimétrica y depende del estado de cocina
de cada ítem: solo vuelve al stock lo que cocina no llegó a preparar. Ver
`cancel_order`."""
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.core.audit import record_audit
from app.core.crud import get_or_404
from app.core.models import User
from app.core.timezone import utc_now
from app.models.dining_table import DiningTable
from app.models.table_session import TableSession
from app.models.session_participant import SessionParticipant
from app.models.cart import Cart
from app.models.option import Option
from app.catalog_engine import ChosenOption, compute_line_price, load_valid_options
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.cash_shift import CashShift
from app.models.sale import Sale
from app.models.customer_order import CustomerOrder
from app.models.order_item import EN_CURSO, OrderItem
from app.models.order_cancel_log import OrderCancelLog
from app.models.order_payment_attempt import OrderPaymentAttempt
from app.models.payment import PaymentMethod
from app.api.v1.sales.builder import SaleLine, build_sale, compute_total, ensure_open_shift
from app.api.v1.sales.schemas import PaymentIn
from app.api.v1.orders.consumption import deduct_order_items, reverse_order_items
from app.api.v1.orders.schemas import (
    BlockIn, CancelIn, CheckoutAndSendIn, PayIn,
    BillResponse, BillOrderLine, BillItemLine, BillSessionLine,
    CheckoutPreviewResponse, DraftPreviewIn,
)
from app.api.v1.promotions import service as promotions
from app.api.v1.orders.service import order_has_sale

logger = logging.getLogger(__name__)

TERMINAL = ("pagada", "cancelada")

# Estados en los que el insumo YA se combinó físicamente: cancelar no lo
# devuelve al stock, es pérdida. El 'out' de la confirmación ya representa esa
# pérdida, así que tampoco se escribe un movimiento extra (sería doble descuento).
_CONSUMED_KITCHEN = ("en_preparacion", "listo")

# Estados de orden en los que el inventario todavía NO se descontó: el descuento
# ocurre al confirmar. Cancelar desde aquí no genera ningún movimiento.
_NOT_DEDUCTED = ("recibida",)


def _item_options(db: Session, item: OrderItem) -> list[ChosenOption]:
    quantities = {o.option_id: o.quantity for o in item.options}
    if not quantities:
        return []
    options = db.execute(select(Option).where(Option.id.in_(quantities.keys()))).scalars().all()
    return [ChosenOption(opt, quantities[opt.id]) for opt in options]


def _reload_order(db: Session, order_id: UUID) -> CustomerOrder:
    return db.execute(
        select(CustomerOrder)
        .options(
            selectinload(CustomerOrder.items).selectinload(OrderItem.options),
            selectinload(CustomerOrder.payment_attempts)
            .selectinload(OrderPaymentAttempt.payment_method),
        )
        .where(CustomerOrder.id == order_id)
    ).scalar_one()


# --------------------------------------------------------------------- Bloqueo

def block_order(db: Session, order_id: UUID, data: BlockIn) -> CustomerOrder:
    try:
        order = db.execute(
            select(CustomerOrder).where(CustomerOrder.id == order_id).with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
        if order.status != "abierta":
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"La orden no está abierta (status={order.status})"
            )
        if order.version != data.version:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"error": "Conflicto de versión (la orden cambió)", "version_actual": order.version},
            )

        pendientes = db.execute(
            select(OrderItem).where(
                OrderItem.order_id == order.id,
                OrderItem.estado_cocina.in_(EN_CURSO),
            )
        ).scalars().all()
        if pendientes:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "error": "Hay ítems sin terminar en cocina; anúlalos o espera a que estén listos.",
                    "items": [
                        {
                            "order_item_id": str(it.id),
                            "product_variant_id": str(it.product_variant_id),
                            "estado_cocina": it.estado_cocina,
                            "participant_id": str(it.participant_id) if it.participant_id else None,
                        }
                        for it in pendientes
                    ],
                },
            )

        order.status = "bloqueada"
        order.version += 1
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error bloqueando orden para cobro")
        raise

    return _reload_order(db, order_id)


# ----------------------------------------------------------------- Cuenta/split

def compute_bill(db: Session, table_id: UUID) -> BillResponse:
    table = get_or_404(db, DiningTable, table_id, "Table not found")

    orders = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items))
        .where(
            CustomerOrder.dining_table_id == table.id,
            CustomerOrder.status != "cancelada",
        )
        .order_by(CustomerOrder.created_at)
    ).scalars().all()

    # nombres de comensal por sesión
    names = dict(db.execute(
        select(SessionParticipant.id, SessionParticipant.display_label)
    ).all())

    total = Decimal("0")
    order_lines: list[BillOrderLine] = []
    split: dict[UUID | None, Decimal] = {}

    for order in orders:
        subtotal = Decimal("0")
        items: list[BillItemLine] = []
        for it in order.items:
            if it.estado_cocina == "anulado":
                continue
            line_total = Decimal(it.unit_price) * it.quantity
            subtotal += line_total
            split[it.participant_id] = split.get(it.participant_id, Decimal("0")) + line_total
            items.append(BillItemLine(
                order_item_id=it.id, product_variant_id=it.product_variant_id,
                participant_id=it.participant_id, quantity=it.quantity,
                unit_price=it.unit_price, line_total=line_total,
                estado_cocina=it.estado_cocina,
            ))
        total += subtotal
        order_lines.append(BillOrderLine(
            order_id=order.id, status=order.status, subtotal=subtotal, items=items,
        ))

    split_lines = [
        BillSessionLine(
            participant_id=sid,
            display_label=names.get(sid) if sid else None,
            subtotal=amount,
        )
        for sid, amount in split.items()
    ]

    return BillResponse(
        dining_table_id=table.id, total=total, orders=order_lines, split=split_lines,
    )


# ------------------------------------------------------------------------ Pago

#: Centinela para "sin filtrar por comensal". No sirve `None`: en el cobro split,
#: `participant_id=None` significa justamente "las líneas sin comensal asignado"
#: (las que metió el mesero), que es un filtro `IS NULL`, no la ausencia de filtro.
ALL_PARTICIPANTS = object()


def order_sale_lines(
    db: Session, order_id: UUID, *, participant_id=ALL_PARTICIPANTS
) -> list[SaleLine]:
    """Líneas cobrables de un pedido, convertidas al formato del constructor de
    venta (con el snapshot inmutable de descripción y opciones).

    Con `participant_id` devuelve solo las de ese comensal (o las sin asignar si
    se pasa `None`): es lo que hace posible el cobro `split`, una venta por
    persona."""
    stmt = (
        select(OrderItem)
        .options(selectinload(OrderItem.options))
        .where(OrderItem.order_id == order_id, OrderItem.estado_cocina != "anulado")
    )
    if participant_id is not ALL_PARTICIPANTS:
        stmt = stmt.where(OrderItem.participant_id == participant_id)

    lines: list[SaleLine] = []
    for it in db.execute(stmt).scalars():
        variant = db.get(ProductVariant, it.product_variant_id)
        product = db.get(Product, variant.product_id) if variant else None
        description = (
            f"{product.name} - {variant.name}" if product
            else (variant.name if variant else "")
        )
        lines.append(SaleLine(
            product_variant_id=it.product_variant_id,
            description=description,
            options=[
                {"option_id": str(chosen.option.id), "name": chosen.option.name,
                 "extra_price": str(chosen.option.extra_price), "quantity": chosen.quantity}
                for chosen in _item_options(db, it)
            ],
            quantity=it.quantity,
            unit_price=Decimal(it.unit_price),
            combo_id=it.combo_id,
            line_id=it.id,
        ))
    return lines


def promo_lines_for(db: Session, lines: list[SaleLine]) -> list[dict]:
    """`promo_lines` para `evaluate_variant_sets` (spec 063,
    contracts/motor-y-persistencia.md §1): un dict por línea con
    `product_variant_id` (pertenencia al conjunto), `unit_price`, `quantity`,
    `line_id` (desempate determinista), `_variant_active` (FR-011), `combo_id`
    (filtro defensivo — histórico) y `description` (para `applied_promotions`).

    Ya NO se traen `product_id` / `category_id` / `presentation_id` (targets y
    presentación eliminados, FR-003 / FR-027)."""
    promo_lines: list[dict] = []
    for line in lines:
        variant = db.get(ProductVariant, line.product_variant_id)
        promo_lines.append({
            "product_variant_id": line.product_variant_id,
            "unit_price": line.unit_price,
            "quantity": line.quantity,
            "line_id": getattr(line, "line_id", None),
            "combo_id": line.combo_id,
            "_variant_active": bool(variant.active) if variant else False,
            "description": line.description,
        })
    return promo_lines


def auto_discount(
    db: Session, lines: list[SaleLine], now: datetime,
) -> tuple[Decimal, UUID | None, list[dict]]:
    """`(descuento_automático_total, promotion_id, applied)` — motor por conjunto
    de variantes (spec 063). `promotion_id = applied[0].promotion_id` si una sola
    promoción explica el descuento, si no `None` (`applied_promotions` lo cubre —
    A-29 resuelto). `applied` es el snapshot serializable para
    `Sale/Invoice/CustomerOrder.applied_promotions` (FR-021)."""
    r = promotions.evaluate_variant_sets(db, promo_lines_for(db, lines), now)
    return r.total, r.single_promotion_id, promotions.applied_to_dicts(r.applied)


def promotion_evaluation_instant(
    orders: list[CustomerOrder], *, now: datetime,
) -> datetime:
    """spec 073 (FR-009/FR-012/FR-012a, A-70): el instante contra el que se
    evalúa la vigencia **TEMPORAL** de las promociones. El más antiguo
    `promotion_evaluated_at` no nulo de `orders` (FR-012a: rondas sucesivas de
    una misma cuenta usan un único instante, el del pedido más antiguo
    pendiente). Si ninguna orden lo tiene (todas anteriores a esta spec,
    FR-012), cae a `now` — la hora del cobro, comportamiento actual sin cambios.

    Todo aware UTC: la columna es `DateTime(timezone=True)` (D1) y `now` en cada
    call site es `datetime.now(timezone.utc)` / `utc_now()`. `min()` y el retorno
    son homogéneos; `local_now()` los convierte a hora local del tenant. El
    guard defensivo normaliza cualquier naive por si un call site futuro lo
    pasara.

    Para `group_bill` (mesas fusionadas) se llama **por pedido**
    (`promotion_evaluation_instant([o], now=now)` dentro del bucle), no sobre
    todo el grupo (FR-018a): esos pedidos se cobran individualmente y el preview
    consolidado debe coincidir con el cobro per-pedido."""
    now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    frozen = [
        (
            o.promotion_evaluated_at
            if o.promotion_evaluated_at.tzinfo is not None
            else o.promotion_evaluated_at.replace(tzinfo=timezone.utc)
        )
        for o in orders
        if o.promotion_evaluated_at is not None
    ]
    return min(frozen) if frozen else now


def compute_checkout_preview(db: Session, order_id: UUID) -> CheckoutPreviewResponse:
    """spec 073 (FR-001…FR-007a): desglose autoritativo de un pedido **ya
    persistido** y aún no cobrado — mesa individual atendida por la Terminal,
    para llevar o domicilio. Solo lectura: sin `db.commit()`, sin `build_sale`,
    sin lock, sin turno de caja.

    Reusa literalmente el mismo motor que el cobro real (`order_sale_lines` +
    `auto_discount` + `compute_total`), evaluando la vigencia contra el instante
    congelado del pedido (`promotion_evaluation_instant`, FR-009/FR-012) — el
    monto que este preview muestra es el que `pay_order`/`checkout_and_send`
    cobrará. Contrato: contracts/preview-cobro-pedido.md."""
    order = get_or_404(db, CustomerOrder, order_id, "Order not found")
    if order.status in TERMINAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"El pedido no está en un estado cobrable (status={order.status})",
        )

    lines = order_sale_lines(db, order.id)
    raw_subtotal = sum((line.line_total for line in lines), start=Decimal("0"))

    instant = promotion_evaluation_instant([order], now=datetime.now(timezone.utc))
    promo_discount, _, _ = auto_discount(db, lines, instant)

    delivery_fee = order.delivery_fee or Decimal("0")
    total = max(
        Decimal("0"),
        compute_total(raw_subtotal, promo_discount, delivery_fee=delivery_fee),
    )

    return CheckoutPreviewResponse(
        subtotal=raw_subtotal,
        discount=promo_discount,
        delivery_fee=delivery_fee,
        total=total,
        promotion_evaluated_at=instant,
    )


def compute_draft_preview(db: Session, data: DraftPreviewIn) -> CheckoutPreviewResponse:
    """spec 073 (FR-013 a FR-015a): desglose de un pedido **hipotético** —
    todavía sin `CustomerOrder` — mientras el cajero arma una orden manual.
    Reusa el mismo motor que el cobro real (`auto_discount` + `compute_total`),
    construyendo las líneas directamente desde `data.items` con el mismo precio
    unitario que `create_order` pondría en `OrderItem.unit_price`
    (`compute_line_price`), para que el subtotal coincida centavo a centavo con
    el que tendrá el pedido una vez creado.

    `promotion_evaluated_at` es siempre la hora de la llamada (sin congelar): el
    borrador no tiene todavía un pedido cuyo instante congelar — el pedido
    congela el suyo al crearse (FR-008). Contrato:
    contracts/preview-borrador-orden-manual.md."""
    lines: list[SaleLine] = []
    for item in data.items:
        variant = db.get(ProductVariant, item.product_variant_id)
        if variant is None or not variant.active:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Variante inexistente o inactiva: {item.product_variant_id}",
            )
        options = load_valid_options(db, item.options, variant=variant)
        product = db.get(Product, variant.product_id)
        description = f"{product.name} - {variant.name}" if product else variant.name
        lines.append(SaleLine(
            product_variant_id=item.product_variant_id,
            description=description,
            options=[
                {"option_id": str(chosen.option.id), "name": chosen.option.name,
                 "extra_price": str(chosen.option.extra_price), "quantity": chosen.quantity}
                for chosen in options
            ],
            quantity=item.quantity,
            unit_price=compute_line_price(variant, options),
        ))

    raw_subtotal = sum((line.line_total for line in lines), start=Decimal("0"))
    now = datetime.now(timezone.utc)  # sin congelar (FR-008)
    promo_discount, _, _ = auto_discount(db, lines, now)
    delivery_fee = data.delivery_fee or Decimal("0")
    total = max(
        Decimal("0"),
        compute_total(raw_subtotal, promo_discount, delivery_fee=delivery_fee),
    )

    return CheckoutPreviewResponse(
        subtotal=raw_subtotal,
        discount=promo_discount,
        delivery_fee=delivery_fee,
        total=total,
        promotion_evaluated_at=now,
    )


def pay_order(db: Session, order_id: UUID, data: PayIn, cashier: User) -> Sale:
    order = get_or_404(db, CustomerOrder, order_id, "Order not found")
    if order.status != "bloqueada":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "La orden debe estar bloqueada para cobrar (bloquea primero).",
        )
    shift = ensure_open_shift(db, data.cash_shift_id)

    try:
        # spec 073 (FR-009, A-70): la vigencia temporal se evalúa contra el
        # instante congelado del pedido (o la hora del cobro si es anterior a
        # esta spec — FR-012).
        instant = promotion_evaluation_instant([order], now=datetime.now(timezone.utc))
        lines = order_sale_lines(db, order.id)

        # Descuento automático (RF-012): motor por conjunto de variantes (spec 063).
        promo_discount, final_promotion_id, applied = auto_discount(db, lines, instant)

        sale = build_sale(
            db,
            lines=lines,
            shift=shift,
            cashier=cashier,
            payments=data.payments,
            discount=Decimal(data.discount) + promo_discount,
            tax=data.tax, tip=data.tip,
            delivery_fee=order.delivery_fee or Decimal("0"),
            customer_name=order.customer_name,
            dining_table_id=order.dining_table_id,
            table_session_id=order.table_session_id,
            participant_id=order.participant_id,
            customer_order_id=order.id,
            promotion_id=final_promotion_id,
            applied_promotions=applied,
            promotion_evaluated_at=instant,  # FR-011a
        )
        order.status = "pagada"
        # FR-021: el descuento agregado + la lista de promociones también en la orden.
        order.discount = Decimal(data.discount) + promo_discount
        order.applied_promotions = applied
        # NO se descuenta inventario aquí: ya se hizo al confirmar el pedido.

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error cobrando la orden de mesa")
        raise

    return db.execute(
        select(Sale)
        .options(selectinload(Sale.items), selectinload(Sale.payments))
        .where(Sale.id == sale.id)
    ).scalar_one()


# --------------------------------------------------------------- Confirmación

def _deduct_and_open(db: Session, order: CustomerOrder, user: User) -> CustomerOrder:
    """Núcleo de `recibida` → `abierta`: descuenta inventario y abre el pedido
    a cocina, sobre una orden **ya cargada** (con o sin lock — lo decide quien
    llama) y con `order.items` ya en memoria. Sin `commit`/`rollback` propios.

    Extraído de `_confirm_order_impl` (spec 028, T016) para que
    `checkout_and_send` pueda reusar exactamente el mismo descuento de
    inventario sin pasar por la exigencia de `OrderPaymentAttempt` confirmado
    (spec 024, FR-017), que es exclusiva del comensal anónimo por QR: aquí el
    cobro ya se resolvió en la misma llamada, vía `build_sale`, no vía un
    intento de pago aparte."""
    entries = [
        (it, _item_options(db, it))
        for it in order.items
        if it.estado_cocina != "anulado"
    ]
    if not entries:
        raise HTTPException(status.HTTP_409_CONFLICT, "El pedido no tiene ítems")

    deduct_order_items(db, entries, user.id, reference_id=order.id)

    order.status = "abierta"
    order.version += 1
    return order


def _confirm_order_impl(db: Session, order_id: UUID, user: User) -> CustomerOrder:
    """Lógica de `recibida` → `abierta` (descuento de inventario incluido), sin
    `commit`/`rollback` propios — quien la invoque decide la frontera de la
    transacción. Extraída de `confirm_order` (spec 026, FR-001/FR-002,
    research.md Decisión 1) para que `confirm_cash_payment_attempt` y
    `approve_payment_attempt` puedan ejecutarla dentro de su propia transacción
    y así confirmar el pago y enviar el pedido a cocina de forma atómica: si
    falta stock, el `rollback` del llamador revierte también la confirmación
    del pago, sin dejar nada a medias.

    Mismas consultas y mismo orden de validación que antes (spec 028, T016):
    solo se movió el descuento propiamente dicho a `_deduct_and_open`, sin
    tocar el resto de este cuerpo — el comportamiento observable de los tres
    llamadores actuales (`confirm_order`, `approve_payment_attempt`,
    `confirm_cash_payment_attempt`) no cambia."""
    order = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
        .with_for_update(of=CustomerOrder)
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order.status != "recibida":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Solo se confirman pedidos en 'recibida' (status={order.status})",
        )

    # spec 024, FR-017: una orden solo avanza a comanda con un intento de pago
    # confirmado. Se verifica antes de tocar inventario a propósito — si el
    # pago no está confirmado, esta llamada no debe tener ningún efecto
    # secundario (research.md spec 024, Decisión 5).
    has_confirmed_payment = db.execute(
        select(OrderPaymentAttempt.id).where(
            OrderPaymentAttempt.order_id == order.id,
            OrderPaymentAttempt.status == "confirmado",
        )
    ).scalar_one_or_none()
    if has_confirmed_payment is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "La orden no tiene un pago confirmado"
        )

    return _deduct_and_open(db, order, user)


def confirm_order(db: Session, order_id: UUID, user: User) -> CustomerOrder:
    """`recibida` → `abierta`: el staff acepta el pedido que envió el comensal y
    **aquí es donde se compromete el inventario**.

    Es el único punto de descuento de los pedidos por QR (`_confirm_order_impl`).
    La validación de stock es la real (con lock por fila, en orden canónico de id
    para no deadlockear); el chequeo del carrito era solo preventivo y pudo quedar
    obsoleto. Si falta stock de un solo insumo, la transacción entera hace
    rollback y el pedido sigue `recibida`, listo para reintentar o cancelar.

    Endpoint público (`POST /orders/{id}/confirm`) sin cambios de contrato. Desde
    spec 026, el flujo normal de la Terminal de Mesas ya no lo invoca
    manualmente — confirmar el pago (`confirm_cash_payment_attempt`/
    `approve_payment_attempt`) dispara esta misma lógica automáticamente. Se
    mantiene expuesto como vía de recuperación (spec 026, research.md
    Decisión 2)."""
    try:
        _confirm_order_impl(db, order_id, user)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error confirmando el pedido")
        raise

    return _reload_order(db, order_id)


# ---------------------------------------------------------- Cobra y envía (T016)

def checkout_and_send(db: Session, order_id: UUID, data: CheckoutAndSendIn, cashier: User) -> Sale:
    """Cobra y envía a cocina, en una sola transacción, una comanda creada con
    `hold_for_payment=True` (spec 028, T012/T013): el mostrador/mesero cobra
    **antes** de mandar el pedido a preparar, así que aquí se funden en un
    solo paso lo que en el flujo QR son dos pasos separados y con su propio
    `OrderPaymentAttempt` — confirmar el pago y despachar a cocina
    (`_confirm_order_impl`, spec 026 research.md Decisión 1).

    Reusa el mismo motor de venta que `pay_order` (líneas, promociones/combos,
    `build_sale`) y el mismo núcleo de descuento que `_confirm_order_impl`
    (`_deduct_and_open`), sin su exigencia de `OrderPaymentAttempt` confirmado
    —irrelevante aquí, porque el cobro ya ocurrió en esta misma llamada—.
    Toma el lock de fila igual que `block_order`/`_confirm_order_impl`, y el
    chequeo de `version` es la guarda de idempotencia contra un doble clic: un
    segundo intento con la misma versión ya vencida es 409, sin crear una
    segunda venta. Si falta stock al enviar a cocina, la transacción entera
    revierte —tampoco queda registrada la venta— igual que si `build_sale`
    hubiera fallado."""
    try:
        order = db.execute(
            select(CustomerOrder)
            .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
            .where(CustomerOrder.id == order_id)
            .with_for_update(of=CustomerOrder)
        ).scalar_one_or_none()
        if order is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
        if order.status != "recibida":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Solo se cobra y envía pedidos en 'recibida' (status={order.status})",
            )
        if order.version != data.version:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"error": "Conflicto de versión (la orden cambió)", "version_actual": order.version},
            )

        shift = ensure_open_shift(db, data.cash_shift_id)

        # spec 073 (FR-009, A-70): igual que `pay_order` — vigencia temporal
        # contra el instante congelado del pedido.
        instant = promotion_evaluation_instant([order], now=datetime.now(timezone.utc))
        lines = order_sale_lines(db, order.id)

        # Descuento automático (RF-012), igual que `pay_order`: motor por
        # conjunto de variantes (spec 063).
        promo_discount, final_promotion_id, applied = auto_discount(db, lines, instant)

        sale = build_sale(
            db,
            lines=lines,
            shift=shift,
            cashier=cashier,
            payments=data.payments,
            discount=Decimal(data.discount) + promo_discount,
            tax=data.tax, tip=data.tip,
            delivery_fee=order.delivery_fee or Decimal("0"),
            customer_name=data.billing_customer_name or "Consumidor Final",
            dining_table_id=order.dining_table_id,
            table_session_id=order.table_session_id,
            participant_id=order.participant_id,
            customer_order_id=order.id,
            promotion_id=final_promotion_id,
            applied_promotions=applied,
            promotion_evaluated_at=instant,  # FR-011a
        )
        order.discount = Decimal(data.discount) + promo_discount
        order.applied_promotions = applied

        # Envía a cocina en la misma transacción: si falta stock, el
        # `rollback` de abajo revierte también la venta recién construida —
        # no queda ni cobrada ni a medio enviar.
        _deduct_and_open(db, order, cashier)

        # spec 035, A-52 (registro-de-anomalias.md): a diferencia de
        # `_deduct_and_open` en sus otros dos llamadores (QR, sin venta
        # todavía en ese instante), aquí la `Sale` ya se construyó arriba en
        # esta misma transacción — el pedido queda `'pagada'` de una vez, no
        # `'abierta'`. `tables_advanced.py` ya no trata `'pagada'` como "sin
        # nada pendiente": sigue bloqueando mientras queden ítems por
        # preparar.
        order.status = "pagada"

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error cobrando y enviando la orden a cocina")
        raise

    return db.execute(
        select(Sale)
        .options(selectinload(Sale.items), selectinload(Sale.payments))
        .where(Sale.id == sale.id)
    ).scalar_one()


# ------------------------------------------------------------------ Cancelación

def cancel_order(
    db: Session,
    order_id: UUID,
    data: CancelIn,
    user: User | None,
    participant: SessionParticipant | None = None,
) -> CustomerOrder:
    """Cancela un pedido y ajusta el inventario **según lo que se alcanzó a
    consumir**, no como una reversa simétrica:

    - orden aún sin confirmar (`recibida`): nunca se descontó → cero movimientos;
    - ítem `pendiente`: descontado pero no preparado → entrada real ('in');
    - ítem `en_preparacion`/`listo`: el insumo ya se combinó → **no vuelve al
      stock**. El 'out' de la confirmación ya es esa pérdida; escribir otro
      movimiento la descontaría dos veces. Se traza en `audit_logs`;
    - ítem `anulado`: ya lo resolvió `void_item`.

    Devolver todo al stock (comportamiento anterior) sobrestimaba el inventario en
    silencio hasta el conteo físico.

    El actor es `user` (staff) **o** `participant` (el propio comensal desde el QR,
    que no es un usuario del sistema). Quién puede cancelar en qué estado lo decide
    quien llama: aquí no hay restricción de estado más allá de los terminales,
    porque el staff sí puede cancelar en cualquier momento.
    """
    order = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order.status in TERMINAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"La orden ya es terminal (status={order.status})"
        )
    # Spec 029, hotfix #4: en los caminos QR/mostrador el pago NO mueve
    # `status` a "pagada" (research.md D2) — sin este chequeo, este endpoint
    # podía cancelar un pedido ya cobrado y dejar su `Sale` huérfana.
    if order_has_sale(db, order_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El pedido ya fue pagado y no puede rechazarse"
        )

    deducted = order.status not in _NOT_DEDUCTED

    try:
        a_revertir: list[tuple[OrderItem, list[ChosenOption]]] = []
        perdidos: list[dict] = []

        for it in order.items:
            if it.estado_cocina == "anulado":
                # Ya se resolvió al anular el ítem (void_item revirtió si procedía).
                continue
            if not deducted:
                # La orden nunca llegó a descontar stock: no hay nada que revertir.
                continue
            if it.estado_cocina in _CONSUMED_KITCHEN:
                # Insumo ya consumido: no vuelve a stock. Se registra como pérdida.
                perdidos.append({
                    "order_item_id": str(it.id),
                    "product_variant_id": str(it.product_variant_id),
                    "quantity": it.quantity,
                    "estado_cocina": it.estado_cocina,
                })
                continue
            a_revertir.append((it, _item_options(db, it)))

        actor_id = user.id if user is not None else None
        reverse_order_items(db, a_revertir, actor_id, reference_id=order.id)
        revertidos = [str(it.id) for it, _ in a_revertir]

        order.status = "cancelada"
        db.add(OrderCancelLog(
            order_id=order.id, motivo=data.motivo,
            user_id=actor_id,
            user_name=user.name if user is not None else None,
            participant_id=participant.id if participant is not None else None,
        ))

        # Spec 044: rechazar un pedido con pago QR pendiente (efectivo, o
        # transferencia sin comprobante aún) también resuelve ese intento —
        # sin esto quedaba "pendiente" para siempre en una orden ya
        # cancelada. Mismos campos que `reject_payment_attempt` (arriba),
        # pero buscado por `order_id` porque este endpoint no recibe
        # `attempt_id`; el índice único parcial garantiza a lo sumo un
        # `pendiente` por orden, así que `scalar_one_or_none()` es seguro.
        pending_attempt = db.execute(
            select(OrderPaymentAttempt)
            .where(
                OrderPaymentAttempt.order_id == order.id,
                OrderPaymentAttempt.status == "pendiente",
            )
            .with_for_update(of=OrderPaymentAttempt)
        ).scalar_one_or_none()
        if pending_attempt is not None:
            pending_attempt.status = "rechazado"
            pending_attempt.rejection_reason = data.motivo
            pending_attempt.resolved_by_user_id = actor_id
            pending_attempt.resolved_at = utc_now()

        if perdidos:
            # La pérdida no genera movimiento de inventario (ya está descontada);
            # queda trazada en auditoría para el reporte de mermas.
            logger.warning(
                "Cancelación de orden %s con %d ítem(s) ya consumidos: pérdida sin reversa",
                order.id, len(perdidos),
            )
            record_audit(
                db,
                action="order.cancel.loss",
                entity="customer_orders",
                entity_id=order.id,
                user=user,
                payload={
                    "motivo": data.motivo,
                    "items_perdidos": perdidos,
                    "items_revertidos": revertidos,
                    "cancelado_por_comensal": participant.id is not None
                    if participant is not None else False,
                },
            )

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error cancelando la orden")
        raise

    return _reload_order(db, order_id)


# ------------------------------------------------------------- Liberar mesa

def close_participants(db: Session, ts: TableSession) -> int:
    """Echa a los comensales de una sesión y abandona sus carritos, **sin tocar la
    sesión**. Devuelve cuántos cerró.

    Se usa suelta cuando una sesión lleva demasiado abierta pero todavía tiene algo
    que cobrar: no interesa que nadie siga pidiendo con un token viejo, pero cerrar
    la sesión dejaría la mesa sin forma de cobrarse.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    participants = db.execute(
        select(SessionParticipant).where(
            SessionParticipant.table_session_id == ts.id,
            SessionParticipant.status == "open",
        )
    ).scalars().all()

    for p in participants:
        p.status = "closed"
        if p.closed_at is None:
            p.closed_at = now
        for cart in db.execute(
            select(Cart).where(Cart.participant_id == p.id, Cart.status == "abierto")
        ).scalars():
            cart.status = "abandonado"

    return len(participants)


def close_table_sessions(
    db: Session, table_id: UUID, *, closed_by: User | None = None
) -> list[TableSession]:
    """Cierra en cascada las sesiones `active` de una mesa: la sesión de mesa, sus
    comensales y los carritos que quedaran abiertos.

    No hace commit (se une a la transacción del caller) ni valida órdenes
    pendientes — eso es responsabilidad de quien llama (`release_table`, el cierre
    con `billing_mode`, o el job de sesiones huérfanas, que pasa `closed_by=None`).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    sessions = db.execute(
        select(TableSession).where(
            TableSession.dining_table_id == table_id,
            TableSession.status == "active",
        )
    ).scalars().all()

    for ts in sessions:
        ts.status = "closed"
        if ts.closed_at is None:
            ts.closed_at = now
        if closed_by is not None:
            ts.closed_by_user_id = closed_by.id
            ts.closed_by_user_name = closed_by.name

        close_participants(db, ts)

    return sessions


def delete_orphan_carts(db: Session, sessions: list[TableSession]) -> None:
    """Elimina físicamente los `Cart` de los participantes de `sessions`, sin
    importar su `status` (spec 039, FR-001/FR-002/FR-004). Se invoca en el
    call-site, exactamente donde la mesa ya quedó `libre` — nunca dentro de
    `close_table_sessions`/`close_participants` (research.md Decisión 1), para no
    borrar carritos cuando la mesa no termina liberándose (`_sweep_schema`,
    RN-SCHED-04). No hace `commit()`/`flush()` propio: se une a la transacción
    del caller.
    """
    if not sessions:
        return

    participant_ids = select(SessionParticipant.id).where(
        SessionParticipant.table_session_id.in_([s.id for s in sessions])
    )
    db.execute(delete(Cart).where(Cart.participant_id.in_(participant_ids)))


def release_table(
    db: Session, table_id: UUID, *, closed_by: User | None = None
) -> DiningTable:
    table = get_or_404(db, DiningTable, table_id, "Table not found")

    blocking = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items))
        .where(
            CustomerOrder.dining_table_id == table.id,
            CustomerOrder.status.notin_(TERMINAL),
        )
    ).scalars().all()
    if blocking:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "La mesa tiene órdenes sin cerrar (paga o cancela primero).",
                "orders": [
                    {
                        "order_id": str(o.id),
                        "status": o.status,
                        "items": len([i for i in o.items if i.estado_cocina != "anulado"]),
                    }
                    for o in blocking
                ],
            },
        )

    try:
        table.status = "libre"
        sessions = close_table_sessions(db, table.id, closed_by=closed_by)
        delete_orphan_carts(db, sessions)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Error liberando la mesa")
        raise

    db.refresh(table)
    return table


# ------------------------------------------------------- Intentos de pago (spec 024)
# Revisión del cajero: aprobar/rechazar comprobante, confirmar efectivo (US2/US3).
# `confirm_order` (arriba) es el único punto que hace avanzar la orden a comanda;
# estas tres funciones solo resuelven el intento, nunca tocan `CustomerOrder.status`
# ni descuentan inventario (contracts/cashier-payment-review.md).

def _load_pending_attempt_for_update(db: Session, attempt_id: UUID) -> OrderPaymentAttempt:
    """Bloqueo pesimista sobre el intento, solo si sigue `pendiente` — mismo
    patrón que `confirm_order` usa sobre la orden (research.md spec 024,
    Decisión 9). Garantiza FR-018/SC-007: una segunda resolución casi
    simultánea del mismo intento no tiene efecto."""
    attempt = db.execute(
        select(OrderPaymentAttempt)
        .where(OrderPaymentAttempt.id == attempt_id)
        .with_for_update(of=OrderPaymentAttempt)
    ).scalar_one_or_none()
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Intento de pago no encontrado")
    if attempt.status != "pendiente":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"El intento de pago ya fue resuelto (status={attempt.status})",
        )
    return attempt


def list_payment_attempts(db: Session, order_id: UUID) -> list[OrderPaymentAttempt]:
    """Historial completo de intentos de una orden, para cajero/back-office
    (FR-016) — incluye rechazados, con su motivo."""
    get_or_404(db, CustomerOrder, order_id, "Order not found")
    return db.execute(
        select(OrderPaymentAttempt)
        .options(selectinload(OrderPaymentAttempt.payment_method))
        .where(OrderPaymentAttempt.order_id == order_id)
        .order_by(OrderPaymentAttempt.created_at)
    ).scalars().all()


def approve_payment_attempt(
    db: Session, attempt_id: UUID, cash_shift_id: UUID, user: User
) -> OrderPaymentAttempt:
    """Aprueba un comprobante de transferencia (US2, Acceptance Scenario 4).

    Spec 026, FR-001: aprobar el comprobante confirma el intento de pago y, en
    la misma transacción, envía el pedido a cocina (`_confirm_order_impl`) — si
    falta stock, ninguna de las dos cosas queda registrada (FR-002,
    research.md Decisión 1).

    Spec 028: además genera la venta/factura en esta misma transacción (antes
    solo se generaba al "Cobrar y cerrar mesa" — spec 010 `close_session` —,
    botón que esta spec retira del flujo QR; sin esto, un pedido QR nunca
    llegaba a tener una `Sale`/`Invoice`, y "Reimprimir Factura POS" y
    "Liberar Mesa" quedaban permanentemente rotos para ese pedido). Reusa
    exactamente el mismo motor de venta que `pay_order`/`checkout_and_send`
    (líneas, promociones/combos, `build_sale`) — un único pago por el total
    exacto de la orden, con el método del comprobante ya aprobado."""
    try:
        attempt = _load_pending_attempt_for_update(db, attempt_id)
        method = get_or_404(db, PaymentMethod, attempt.payment_method_id, "Payment method not found")
        if method.is_cash:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Un método en efectivo se confirma con confirm-cash, no con approve",
            )
        if not attempt.receipt_file_url:
            raise HTTPException(status.HTTP_409_CONFLICT, "El intento no tiene comprobante todavía")

        shift = ensure_open_shift(db, cash_shift_id)

        attempt.status = "confirmado"
        attempt.resolved_by_user_id = user.id
        attempt.resolved_at = utc_now()
        # La sesión tiene autoflush=False (ver `with_db`); sin este flush,
        # el chequeo `has_confirmed_payment` de `_confirm_order_impl` lee el
        # `status` todavía persistido ("pendiente") y siempre rechaza con
        # 409, aunque el intento ya esté confirmado en memoria.
        db.flush()
        order = _confirm_order_impl(db, attempt.order_id, user)

        # spec 073 (FR-009/FR-018, A-70): vigencia temporal contra el instante
        # congelado del pedido (el flujo QR también lo congela — research.md D2).
        instant = promotion_evaluation_instant([order], now=utc_now())
        lines = order_sale_lines(db, order.id)
        promo_discount, final_promotion_id, applied = auto_discount(db, lines, instant)
        # Spec 056, research.md Decisión 5: el domicilio debe quedar incluido
        # en el ÚNICO pago que se autogenera aquí — de lo contrario queda
        # corto exactamente en ese valor y el propio chequeo `paid < total`
        # de build_sale (ya con el domicilio sumado) rechazaría este pago.
        delivery_fee = order.delivery_fee or Decimal("0")
        total = (
            sum((line.line_total for line in lines), Decimal("0"))
            - promo_discount + delivery_fee
        )

        build_sale(
            db,
            lines=lines,
            shift=shift,
            cashier=user,
            payments=[PaymentIn(payment_method_id=attempt.payment_method_id, amount=total)],
            discount=promo_discount,
            delivery_fee=delivery_fee,
            customer_name=order.customer_name,
            dining_table_id=order.dining_table_id,
            table_session_id=order.table_session_id,
            participant_id=order.participant_id,
            customer_order_id=order.id,
            promotion_id=final_promotion_id,
            applied_promotions=applied,
            promotion_evaluated_at=instant,  # FR-011a
        )
        order.discount = promo_discount
        order.applied_promotions = applied

        # spec 035, A-52 (registro-de-anomalias.md): la Sale ya se construyó
        # arriba en esta misma transacción — igual que en checkout_and_send,
        # el pedido queda 'pagada' de una vez, no 'abierta' (aunque cocina
        # todavía no haya terminado de prepararlo).
        order.status = "pagada"

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error aprobando comprobante de pago")
        raise

    db.refresh(attempt)
    return attempt


def reject_payment_attempt(
    db: Session, attempt_id: UUID, reason: str, user: User
) -> OrderPaymentAttempt:
    """Rechaza un comprobante con motivo obligatorio (FR-014, US2 Acceptance
    Scenario 5-6). El motivo queda visible solo para cajero/back-office
    (Clarification 3) — el router del comensal nunca lo serializa."""
    try:
        attempt = _load_pending_attempt_for_update(db, attempt_id)
        method = get_or_404(db, PaymentMethod, attempt.payment_method_id, "Payment method not found")
        if method.is_cash:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Un método en efectivo se confirma con confirm-cash, no se rechaza",
            )

        attempt.status = "rechazado"
        attempt.rejection_reason = reason
        attempt.resolved_by_user_id = user.id
        attempt.resolved_at = utc_now()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error rechazando comprobante de pago")
        raise

    db.refresh(attempt)
    return attempt


def confirm_cash_payment_attempt(
    db: Session, attempt_id: UUID, amount_received: Decimal, cash_shift_id: UUID, user: User
) -> OrderPaymentAttempt:
    """Confirma un pago en efectivo y calcula el cambio (FR-009/FR-010,
    US3). FR-010a: impide confirmar si `amount_received < total_orden`.

    Spec 026, FR-001: confirmar el efectivo confirma el intento de pago y, en
    la misma transacción, envía el pedido a cocina (`_confirm_order_impl`) — si
    falta stock, ninguna de las dos cosas queda registrada (FR-002,
    research.md Decisión 1).

    Spec 028: además genera la venta/factura en esta misma transacción (mismo
    motivo que `approve_payment_attempt` — ver su docstring). El monto
    recibido en efectivo se manda tal cual a `build_sale`, que calcula
    `change_given` con el mismo criterio (`pagado - total`) que ya usa
    `attempt.change_amount` — ambos coinciden por construcción.

    Spec 073, US7 (FR-021/FR-023, A-70, research.md D13): el chequeo previo del
    "monto recibido" compara contra el `Total` autoritativo —subtotal menos
    descuento por promoción (instante congelado) más domicilio— que devuelve
    `compute_checkout_preview`, la misma función de solo lectura que muestra el
    panel "Pagos por confirmar". Antes usaba `_order_total` (suma sin descuento),
    que rechazaba con 422 un monto que sí cubría la venta con promoción.
    `attempt.change_amount` sale de ese mismo `total` → coincide al peso con
    `Sale.change_given` de `build_sale`."""
    try:
        attempt = _load_pending_attempt_for_update(db, attempt_id)
        method = get_or_404(db, PaymentMethod, attempt.payment_method_id, "Payment method not found")
        if not method.is_cash:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Un método de transferencia se aprueba/rechaza, no se confirma con confirm-cash",
            )

        # spec 073, US7 (FR-023, research.md D13): el `Total` real con descuento
        # por promoción (instante congelado) y domicilio — la misma cuenta
        # autoritativa que el panel muestra y que `build_sale` registrará.
        total = compute_checkout_preview(db, attempt.order_id).total
        if amount_received < total:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"El monto recibido ({amount_received}) es menor al total de la orden ({total})",
            )

        shift = ensure_open_shift(db, cash_shift_id)

        attempt.amount_received = amount_received
        attempt.change_amount = amount_received - total
        attempt.status = "confirmado"
        attempt.resolved_by_user_id = user.id
        attempt.resolved_at = utc_now()
        # Mismo motivo que en `approve_payment_attempt`: autoflush=False no
        # deja ver este `UPDATE` a la `SELECT` de `_confirm_order_impl` sin
        # este flush explícito (mismo patrón que ya usan
        # `table_sessions/service.py` y `sales/service.py`).
        db.flush()
        order = _confirm_order_impl(db, attempt.order_id, user)

        # spec 073 (FR-009/FR-018, A-70): vigencia temporal contra el instante
        # congelado del pedido.
        instant = promotion_evaluation_instant([order], now=utc_now())
        lines = order_sale_lines(db, order.id)
        promo_discount, final_promotion_id, applied = auto_discount(db, lines, instant)

        build_sale(
            db,
            lines=lines,
            shift=shift,
            cashier=user,
            payments=[PaymentIn(payment_method_id=attempt.payment_method_id, amount=amount_received)],
            discount=promo_discount,
            delivery_fee=order.delivery_fee or Decimal("0"),
            customer_name=order.customer_name,
            dining_table_id=order.dining_table_id,
            table_session_id=order.table_session_id,
            participant_id=order.participant_id,
            customer_order_id=order.id,
            promotion_id=final_promotion_id,
            applied_promotions=applied,
            promotion_evaluated_at=instant,  # FR-011a
        )
        order.discount = promo_discount
        order.applied_promotions = applied

        # spec 035, A-52 (registro-de-anomalias.md): igual que en
        # approve_payment_attempt/checkout_and_send — la Sale ya existe en
        # esta misma transacción.
        order.status = "pagada"

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error confirmando pago en efectivo")
        raise

    db.refresh(attempt)
    return attempt
