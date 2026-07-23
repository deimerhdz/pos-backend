"""Seed del menú de la heladería "El Fin del Calor" en un schema de tenant.

Carga el catálogo de la carta (categorías, grupo de opciones "Sabores de crema"
con 25 sabores, grupos de sabor para bebidas, y ~50 productos con su variante y,
cuando aplica, el grupo "N sabores al gusto" vía product_option_groups.min/max).

La carta NO trae precios: se cargan con `--price-default` (0 por defecto) para
editarlos luego desde el módulo de productos/catálogo.

Es idempotente por nombre: reejecutar no duplica.

Uso:
    python -m app.scripts.seed_menu_fin_del_calor --schema heladeria
    python -m app.scripts.seed_menu_fin_del_calor --schema heladeria --price-default 12000
"""
import argparse
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import with_db
from app.models.category import Category
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.option_group import OptionGroup
from app.models.option import Option
from app.models.product_option_group import ProductOptionGroup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Datos de la carta
# ---------------------------------------------------------------------------

CATEGORIES = [
    "Ensaladas de frutas",
    "Copas y especiales",
    "Bananas split",
    "Postres",
    "Malteadas",
    "Batidos",
    "Jugos naturales",
    "Michatas",
    "Limonadas",
    "Granizados",
    "Frappé",
    "Cholados y raspados",
    "Adicionales y bebidas",
]

# Grupo principal: sabores de crema (helado). Las copas eligen N de estos.
SABORES_CREMA = [
    "Arequipe", "Brownie", "Café", "Chicle", "Chocolate", "Combinado", "Fresa",
    "Frutos rojos", "Limón", "Macadamia", "Mandarina", "Maracuyá", "Mora",
    "Nata maní", "Oreo", "Piña", "Ron pasas", "Tres leches", "Vainilla",
    "Vainilla chip", "Vainilla con pasas", "Veteado de cereza", "Veteado de mora",
    "Zapote",
]

# Sabores de bebidas (grupos independientes, single-select).
SABORES_LIMONADA = [
    "Cerezada", "Mango biche", "Kiwi", "Coco", "Sandía limón", "Aguacate",
    "Jengibre y yerba buena", "Natural",
]
SABORES_GRANIZADO = [
    "Café", "Lulo", "Maracuyá", "Sandía limón", "Cereza", "Mora", "Milo",
    "Mango biche", "Limón", "Licor",
]
SABORES_FRAPPE = ["Café", "Fresa", "Chocolate", "Caramelo", "Milo", "Licor"]
SABORES_JUGO = [
    "Zapote", "Níspero", "Lulo", "Milo", "Mora", "Maracuyá", "Borojó",
    "Banano", "Fresa", "Guanábana",
]
SABORES_CHOLADO = ["Lulo", "Maracuyá", "Sandía limón", "Mora", "Fresa"]
SABORES_RASPADO = [
    "Lulo", "Limón", "Maracuyá", "Mora", "Fresa", "Sandía limón", "Cereza",
]
MICHELADA_BASE = ["Frutos rojos", "Frutos verdes", "Frutos amarillos", "Saborizada"]

# Copas / especiales: nombre -> nº de "sabores al gusto" (0 = sabor fijo, sin grupo).
COPAS = {
    "Seducción": 2, "Sueño de Chocolate": 0, "Torre de Helado": 7,
    "Fantasía Amorosa": 4, "Placer de Brevas": 1, "Kumis": 0, "Ponque": 1,
    "Deseos de Durazno": 2, "Canasta": 3, "Mickey": 2, "Pollito": 2,
    "Medusa": 3, "Fresas con Crema": 0, "Salpicón con Helado": 1, "Capuchino": 0,
    "Caribe": 0, "Remolino de Fresas": 0, "Éxtasis": 0, "Delicias de Queso": 1,
    "Reina de la Casa": 4, "Esplendor": 3, "Andina": 3, "Girasol": 0,
    "Explosión de Amor": 3, "Choco Break": 0, "Fiesta": 0, "Ilusión": 0,
    "Furor de Piña": 0, "Dulce Pausa": 1, "Dulces Sueños": 0, "Vísperas del Día": 2,
    "Pecado por Chocolate": 0, "Día Marrosquino": 3, "Principio": 4, "Latidos": 0,
    "Tentación de Fresas": 0, "Incitación de Milo": 0, "Chocolate Supremo": 0,
    "Delicias de Oreo": 0,
}

# Postres (con/ sin sabor al gusto).
POSTRES = {"Antojos": 0, "Señor Brownie": 0, "Señora Brownie": 0}

# Bananas split.
BANANAS = {"Banana Split Sencilla": 3, "Banana Split Especial": 3}

# Ensaladas con tamaño -> se modelan como productos separados (N varía por tamaño).
ENSALADAS = {
    "Ensalada de frutas Grande": 3,
    "Ensalada de frutas Mediana": 2,
    "Ensalada de frutas Pequeña": 1,
    "Ensalada de frutas y frutos secos": 3,
    "Ensalada light": 0,
}

# Malteadas: 1 sabor al gusto.
MALTEADAS = {"Malteada": 1}

# Batidos: recetas fijas (sin sabor al gusto).
BATIDOS = [
    "Batido de sandía, limón y fresa",
    "Batido de piña, fresa, papaya y banano",
    "Batido de manzana, banano y naranja",
    "Batido de manzana, piña, papaya y avena",
    "Batido de mango, banano y papaya",
]

# "Además": productos empacados simples (sin opciones).
ADICIONALES = [
    "Cono", "Chococono", "Balón", "Galleta", "Paleta en agua", "Paleta en leche",
    "Sundae", "Turrón", "Vasito", "Vaso servido", "Gaseosa", "Agua", "Cerveza",
    "Yogur", "Bonyur", "Gelatina",
]


# ---------------------------------------------------------------------------
# Helpers idempotentes
# ---------------------------------------------------------------------------

def get_or_create_category(db: Session, name: str) -> Category:
    row = db.execute(select(Category).where(Category.name == name)).scalar_one_or_none()
    if row is None:
        row = Category(name=name)
        db.add(row)
        db.flush()
    return row


def get_or_create_option_group(db: Session, name: str, min_s: int, max_s: int) -> OptionGroup:
    row = db.execute(select(OptionGroup).where(OptionGroup.name == name)).scalar_one_or_none()
    if row is None:
        row = OptionGroup(name=name, min_select=min_s, max_select=max_s)
        db.add(row)
        db.flush()
    return row


def ensure_options(db: Session, group: OptionGroup, names: list[str]) -> None:
    existing = {
        o.name for o in db.execute(
            select(Option).where(Option.option_group_id == group.id)
        ).scalars()
    }
    for n in names:
        if n not in existing:
            db.add(Option(option_group_id=group.id, name=n))
    db.flush()


def get_or_create_product(
    db: Session, category: Category, name: str, price: Decimal,
    preparation_type: str = "prepared",
) -> Product:
    row = db.execute(
        select(Product).where(Product.category_id == category.id, Product.name == name)
    ).scalar_one_or_none()
    if row is None:
        row = Product(category_id=category.id, name=name, preparation_type=preparation_type)
        db.add(row)
        db.flush()
        db.add(ProductVariant(product_id=row.id, name="Single", price=price))
        db.flush()
    return row


def attach_option_group(db: Session, product: Product, group: OptionGroup, min_s: int, max_s: int) -> None:
    exists = db.execute(
        select(ProductOptionGroup).where(
            ProductOptionGroup.product_id == product.id,
            ProductOptionGroup.option_group_id == group.id,
        )
    ).scalar_one_or_none()
    if exists is None:
        db.add(ProductOptionGroup(
            product_id=product.id, option_group_id=group.id,
            min_select=min_s, max_select=max_s,
        ))
        db.flush()


# ---------------------------------------------------------------------------
# Seed principal
# ---------------------------------------------------------------------------

def seed(schema: str, price_default: Decimal) -> dict:
    counts = {"categories": 0, "option_groups": 0, "products": 0}
    with with_db(schema) as db:
        cats = {name: get_or_create_category(db, name) for name in CATEGORIES}
        counts["categories"] = len(cats)

        # Grupo principal + grupos de bebidas.
        sabores = get_or_create_option_group(db, "Sabores de crema", 1, 7)
        ensure_options(db, sabores, SABORES_CREMA)

        drink_groups = {
            "Sabor de limonada": SABORES_LIMONADA,
            "Sabor de granizado": SABORES_GRANIZADO,
            "Sabor de frappé": SABORES_FRAPPE,
            "Sabor de jugo": SABORES_JUGO,
            "Sabor de cholado": SABORES_CHOLADO,
            "Sabor de raspado": SABORES_RASPADO,
            "Base de michelada": MICHELADA_BASE,
        }
        groups = {}
        for gname, opts in drink_groups.items():
            g = get_or_create_option_group(db, gname, 1, 1)
            ensure_options(db, g, opts)
            groups[gname] = g
        counts["option_groups"] = 1 + len(groups)

        # Copas / especiales.
        for name, n in COPAS.items():
            p = get_or_create_product(db, cats["Copas y especiales"], name, price_default)
            if n > 0:
                attach_option_group(db, p, sabores, n, n)

        # Postres.
        for name, n in POSTRES.items():
            p = get_or_create_product(db, cats["Postres"], name, price_default)
            if n > 0:
                attach_option_group(db, p, sabores, n, n)

        # Bananas split.
        for name, n in BANANAS.items():
            p = get_or_create_product(db, cats["Bananas split"], name, price_default)
            if n > 0:
                attach_option_group(db, p, sabores, n, n)

        # Ensaladas (tamaño como producto separado).
        for name, n in ENSALADAS.items():
            p = get_or_create_product(db, cats["Ensaladas de frutas"], name, price_default)
            if n > 0:
                attach_option_group(db, p, sabores, n, n)

        # Malteadas (1 sabor al gusto).
        for name, n in MALTEADAS.items():
            p = get_or_create_product(db, cats["Malteadas"], name, price_default)
            if n > 0:
                attach_option_group(db, p, sabores, n, n)

        # Batidos (recetas fijas).
        for name in BATIDOS:
            get_or_create_product(db, cats["Batidos"], name, price_default)

        # Bebidas con grupo de sabor.
        drink_products = [
            ("Jugos naturales", "Jugo natural", "Sabor de jugo"),
            ("Limonadas", "Limonada", "Sabor de limonada"),
            ("Granizados", "Granizado", "Sabor de granizado"),
            ("Frappé", "Frappé", "Sabor de frappé"),
            ("Cholados y raspados", "Cholado", "Sabor de cholado"),
            ("Cholados y raspados", "Raspado", "Sabor de raspado"),
            ("Michatas", "Michelada", "Base de michelada"),
        ]
        for cat_name, prod_name, group_name in drink_products:
            p = get_or_create_product(db, cats[cat_name], prod_name, price_default)
            attach_option_group(db, p, groups[group_name], 1, 1)

        # Adicionales / bebidas empacadas (sin opciones).
        for name in ADICIONALES:
            get_or_create_product(db, cats["Adicionales y bebidas"], name, price_default,
                                  preparation_type="packaged")

        counts["products"] = len(db.execute(select(Product)).scalars().all())

        db.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga el menú 'El Fin del Calor' en un tenant.")
    parser.add_argument("--schema", required=True, help="Schema del tenant (p. ej. heladeria)")
    parser.add_argument("--price-default", type=Decimal, default=Decimal("0"),
                        help="Precio placeholder para las variantes (la carta no trae precios)")
    args = parser.parse_args()

    counts = seed(args.schema, args.price_default)
    logger.info("Menú cargado en schema '%s': %s", args.schema, counts)


if __name__ == "__main__":
    main()
