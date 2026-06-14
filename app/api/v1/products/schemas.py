from enum import Enum
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductType(str, Enum):
    INGREDIENT = "INGREDIENT"
    PRODUCT = "PRODUCT"
    RECIPE = "RECIPE"


class ProductComponentIn(BaseModel):
    component_id: UUID = Field(
        ...,
        description="Identificador del producto-ingrediente que compone la receta.",
    )
    quantity: Decimal = Field(
        ..., gt=0, max_digits=12, decimal_places=3,
        description="Cantidad del ingrediente requerida por una unidad de la receta.",
        examples=["0.250"],
    )


class ProductComponentResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único del componente.")
    component_id: UUID = Field(..., description="Producto-ingrediente que compone la receta.")
    name: str | None = Field(None, description="Nombre del producto-ingrediente.")
    quantity: Decimal = Field(..., description="Cantidad del ingrediente requerida.", examples=["0.250"])

    model_config = ConfigDict(from_attributes=True)


class ProductCreate(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=255,
        description="Nombre del producto.",
        examples=["Coca-Cola 350ml"],
    )
    description: str | None = Field(
        None, max_length=255,
        description="Descripción opcional del producto.",
        examples=["Gaseosa en lata de 350 ml"],
    )
    price: Decimal = Field(
        ..., ge=0, max_digits=10, decimal_places=2,
        description="Precio de venta del producto.",
        examples=["2500.00"],
    )
    cost: Decimal = Field(
        ..., ge=0, max_digits=10, decimal_places=2,
        description="Costo de adquisición del producto.",
        examples=["1500.00"],
    )
    is_menu: bool = Field(
        False,
        description="Indica si el producto forma parte del menú.",
        examples=[False],
    )
    product_type: ProductType = Field(
        ...,
        description="Tipo de producto: INGREDIENT, PRODUCT o RECIPE.",
        examples=["PRODUCT"],
    )
    control_stock: bool = Field(
        False,
        description=(
            "Indica si el producto gestiona inventario. INGREDIENT siempre lo gestiona; "
            "RECIPE nunca; PRODUCT solo si es true."
        ),
        examples=[True],
    )
    stock: int | None = Field(
        None, ge=0,
        description=(
            "Stock inicial del producto. Solo aplica cuando se gestiona inventario "
            "(INGREDIENT o PRODUCT con control_stock=true). Si es mayor a 0, registra un "
            "movimiento de stock inicial."
        ),
        examples=[100],
    )
    stock_min: int = Field(
        0, ge=0,
        description="Stock mínimo del inventario. Por defecto 0 si no se envía.",
        examples=[10],
    )
    category_id: UUID = Field(
        ...,
        description="Identificador de la categoría a la que pertenece el producto.",
    )
    unit_measure_id: UUID = Field(
        ...,
        description="Identificador de la unidad de medida del producto.",
    )
    components: list[ProductComponentIn] | None = Field(
        None,
        description="Lista de ingredientes que componen la receta. Requerido para product_type=RECIPE.",
    )


class ProductUpdate(BaseModel):
    name: str | None = Field(
        None, min_length=1, max_length=255,
        description="Nuevo nombre del producto.",
        examples=["Coca-Cola 500ml"],
    )
    description: str | None = Field(
        None, max_length=255,
        description="Nueva descripción del producto.",
    )
    price: Decimal | None = Field(
        None, ge=0, max_digits=10, decimal_places=2,
        description="Nuevo precio de venta.",
        examples=["3000.00"],
    )
    cost: Decimal | None = Field(
        None, ge=0, max_digits=10, decimal_places=2,
        description="Nuevo costo de adquisición.",
        examples=["1800.00"],
    )
    is_menu: bool | None = Field(
        None,
        description="Indica si el producto forma parte del menú.",
    )
    product_type: ProductType | None = Field(
        None,
        description="Nuevo tipo de producto: INGREDIENT, PRODUCT o RECIPE.",
        examples=["RECIPE"],
    )
    control_stock: bool | None = Field(
        None,
        description="Actualiza si el producto gestiona inventario.",
    )
    stock_min: int | None = Field(
        None, ge=0,
        description="Nuevo stock mínimo del inventario.",
        examples=[10],
    )
    components: list[ProductComponentIn] | None = Field(
        None,
        description="Nueva lista de ingredientes de la receta. Reemplaza por completo los existentes (solo RECIPE).",
    )
    category_id: UUID | None = Field(
        None,
        description="Nueva categoría del producto. Debe existir.",
    )
    unit_measure_id: UUID | None = Field(
        None,
        description="Nueva unidad de medida del producto. Debe existir.",
    )
    active: bool | None = Field(
        None,
        description="Estado activo/inactivo del producto.",
    )


class ProductResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único del producto.")
    name: str = Field(..., description="Nombre del producto.", examples=["Coca-Cola 350ml"])
    description: str | None = Field(None, description="Descripción del producto.")
    price: Decimal = Field(..., description="Precio de venta del producto.", examples=["2500.00"])
    cost: Decimal = Field(..., description="Costo de adquisición del producto.", examples=["1500.00"])
    is_menu: bool = Field(..., description="Indica si el producto forma parte del menú.", examples=[False])
    product_type: ProductType = Field(..., description="Tipo de producto: INGREDIENT, PRODUCT o RECIPE.", examples=["PRODUCT"])
    control_stock: bool = Field(..., description="Indica si el producto gestiona inventario.", examples=[True])
    category_id: UUID = Field(..., description="Categoría a la que pertenece el producto.")
    unit_measure_id: UUID = Field(..., description="Unidad de medida del producto.")
    active: bool = Field(..., description="Indica si el producto está activo.", examples=[True])
    created_at: datetime = Field(..., description="Fecha de creación del registro.")
    updated_at: datetime | None = Field(None, description="Fecha de la última actualización.")

    model_config = ConfigDict(from_attributes=True)


class ProductListResponse(ProductResponse):
    stock: int | None = Field(None, description="Stock actual del producto.", examples=[100])
    stock_min: int | None = Field(None, description="Stock mínimo configurado del producto.", examples=[10])


class ProductDetailResponse(ProductResponse):
    stock: int | None = Field(None, description="Stock actual del producto (null si no gestiona inventario).", examples=[100])
    stock_min: int | None = Field(None, description="Stock mínimo configurado del producto.", examples=[10])
    components: list[ProductComponentResponse] = Field(
        default_factory=list,
        description="Ingredientes que componen la receta (vacío si no es RECIPE).",
    )
