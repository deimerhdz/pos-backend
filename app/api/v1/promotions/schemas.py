from enum import Enum
from uuid import UUID
from datetime import datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PromotionType(str, Enum):
    PERCENT = "percent"
    FIXED = "fixed"
    COMBO = "combo"
    QTY_PRICE = "qty_price"


class PromotionStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    FINISHED = "finished"


def _validate_csv(value: str | None, low: int, high: int, label: str) -> str | None:
    """`days_of_week` era texto libre acotado por longitud: un `"lunes,martes"`
    se guardaba tal cual y la promoción no aplicaba nunca, sin ningún error
    visible."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    nums = []
    for p in parts:
        if not p.isdigit() or not (low <= int(p) <= high):
            raise ValueError(f"{label} debe ser una lista de enteros {low}..{high}")
        nums.append(int(p))
    return ",".join(str(n) for n in sorted(set(nums)))


class _VigenciaMixin(BaseModel):
    @model_validator(mode="after")
    def _normalize_days(self):
        if "days_of_week" in self.model_fields_set:
            self.days_of_week = _validate_csv(self.days_of_week, 0, 6, "days_of_week")
        return self

    @model_validator(mode="after")
    def _time_window_pair(self):
        # Una ventana a medias no significa nada y hace creer al admin que
        # configuró un happy hour.
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time y end_time deben configurarse juntos")
        return self

    @model_validator(mode="after")
    def _date_range(self):
        if self.starts_at and self.ends_at and self.ends_at.date() < self.starts_at.date():
            raise ValueError("ends_at no puede ser anterior a starts_at")
        return self


class TargetIn(BaseModel):
    """Un destino de la promoción, con su precio de paquete opcional.

    `value`/`min_qty` solo aplican a `qty_price`: en NULL, el target hereda los
    de la promoción. Existen para que "2 Ensaladas Grandes por $12.000" y
    "2 Pequeñas por $8.000" quepan en una sola promoción.
    """

    product_id: UUID | None = None
    category_id: UUID | None = None
    value: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    min_qty: int | None = Field(None, ge=2)

    @model_validator(mode="after")
    def _one_scope(self):
        if self.product_id is None and self.category_id is None:
            raise ValueError("Cada target requiere product_id o category_id")
        if self.product_id is not None and self.category_id is not None:
            raise ValueError("Un target apunta a un producto o a una categoría, no a los dos")
        return self

    @property
    def has_pricing(self) -> bool:
        return self.value is not None or self.min_qty is not None


class ComboItemIn(BaseModel):
    product_variant_id: UUID
    quantity: int = Field(1, ge=1)


class _PromotionRules(BaseModel):
    """Reglas que dependen del `type` y que deben valer igual en create y update.

    Antes vivían solo en `PromotionCreate`: el PATCH aceptaba `value=500` sobre
    un `percent` sin chistar.
    """

    @model_validator(mode="after")
    def _percent_range(self):
        if self.type == PromotionType.PERCENT and self.value is not None and self.value > 100:
            raise ValueError("Un descuento porcentual no puede superar 100")
        return self

    @model_validator(mode="after")
    def _qty_price_pack(self):
        if self.type == PromotionType.QTY_PRICE and (self.min_qty or 0) < 2:
            raise ValueError("qty_price requiere min_qty >= 2 (el tamaño del paquete)")
        return self


class PromotionCreate(_VigenciaMixin, _PromotionRules):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    type: PromotionType
    value: Decimal = Field(..., ge=0, max_digits=12, decimal_places=2)
    # Nace en borrador: activar es una acción explícita, no un default.
    status: PromotionStatus = PromotionStatus.DRAFT
    priority: int = Field(0, ge=0, le=1000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    days_of_week: str | None = Field(None, max_length=20, examples=["0,1,2,3,4"])
    start_time: time | None = None
    end_time: time | None = None
    min_qty: int = Field(1, ge=1)
    targets: list[TargetIn] = Field(default_factory=list)
    combo_items: list[ComboItemIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _status_on_create(self):
        if self.status == PromotionStatus.FINISHED:
            raise ValueError("Una promoción no puede crearse finalizada")
        return self

    @model_validator(mode="after")
    def _target_pricing_only_qty_price(self):
        """El precio por target solo lo entiende `_line_discount` en `qty_price`.
        Aceptarlo en los otros tipos dejaría un campo que se guarda y no hace
        nada, que es peor que rechazarlo."""
        if self.type != PromotionType.QTY_PRICE and any(t.has_pricing for t in self.targets):
            raise ValueError(
                "El precio por producto solo aplica a promociones de tipo paquete (qty_price)"
            )
        return self

    @model_validator(mode="after")
    def _qty_price_needs_priced_targets(self):
        """Un paquete ya no tiene precio propio: vive entero en sus destinos.

        Sin esto se podría crear un `qty_price` global o con destinos a medias,
        que `_line_discount` resolvería con descuento 0 — una promoción que se
        guarda, se activa y no hace nada.
        """
        if self.type != PromotionType.QTY_PRICE:
            return self
        if not self.targets:
            raise ValueError(
                "Un paquete necesita al menos un producto o categoría: el precio se define en cada uno"
            )
        if any(t.value is None or t.min_qty is None for t in self.targets):
            raise ValueError(
                "Cada producto o categoría del paquete necesita sus unidades y su precio"
            )
        return self

    @model_validator(mode="after")
    def _combo_items_required(self):
        if self.type == PromotionType.COMBO:
            variant_ids = {it.product_variant_id for it in self.combo_items}
            if len(variant_ids) < 2:
                raise ValueError("Un combo requiere al menos 2 productos distintos en combo_items")
            if len(variant_ids) != len(self.combo_items):
                raise ValueError("combo_items no puede repetir product_variant_id")
            if self.targets:
                raise ValueError("Un combo define su alcance en combo_items, no en targets")
        elif self.combo_items:
            raise ValueError("combo_items solo aplica a promociones type=combo")
        return self


class PromotionUpdate(_VigenciaMixin):
    """Campos escalares. `type`, `targets` y `combo_items` solo se pueden cambiar
    mientras la promoción esté en `draft` (ver `PromotionShapeUpdate`)."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    value: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    priority: int | None = Field(None, ge=0, le=1000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    days_of_week: str | None = Field(None, max_length=20)
    start_time: time | None = None
    end_time: time | None = None
    min_qty: int | None = Field(None, ge=1)


class PromotionShapeUpdate(BaseModel):
    """Cambio de forma: solo válido en `draft`."""
    type: PromotionType | None = None
    targets: list[TargetIn] | None = None
    combo_items: list[ComboItemIn] | None = None


class PromotionStatusUpdate(BaseModel):
    status: PromotionStatus


class PromotionDuplicate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class TargetResponse(BaseModel):
    product_id: UUID | None = None
    category_id: UUID | None = None
    value: Decimal | None = None
    min_qty: int | None = None
    model_config = ConfigDict(from_attributes=True)


class ComboItemResponse(BaseModel):
    product_variant_id: UUID
    quantity: int
    model_config = ConfigDict(from_attributes=True)


class OverlapResponse(BaseModel):
    """Promoción que puede competir con esta sobre el mismo producto."""
    id: UUID
    name: str
    priority: int
    model_config = ConfigDict(from_attributes=True)


class PromotionResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    type: str
    value: Decimal
    status: str
    priority: int
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    days_of_week: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    min_qty: int
    targets: list[TargetResponse] = Field(default_factory=list)
    combo_items: list[ComboItemResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class PromotionWithOverlaps(PromotionResponse):
    """Respuesta de create/update: la promoción más la **advertencia** de con
    quién compite. No es un bloqueo — ver `service.find_overlaps`."""
    overlaps: list[OverlapResponse] = Field(default_factory=list)


class AppliedPromotionResponse(BaseModel):
    """Detalle por línea, para la pantalla del cajero (RF: mostrar promociones
    aplicadas y el detalle del beneficio)."""
    line_index: int
    promotion_id: UUID
    promotion_name: str
    promotion_type: str
    amount: Decimal
    detail: str
