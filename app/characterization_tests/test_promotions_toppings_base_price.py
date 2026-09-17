"""FR-027 (spec 083, sesión 2026-09-17): el descuento `percent` de una
promoción se calcula solo sobre el precio base de la variante, nunca sobre el
precio de los toppings/adicionales elegidos (`OptionGroup`, specs 064/065).

`test_promotions_service.py` ya caracteriza el cambio en el núcleo del motor
(`evaluate_variant_sets`); este módulo caracteriza que `base_unit_price` llega
correctamente calculado a ese motor desde las dos superficies que arman
`promo_lines` con datos reales de opciones: `SaleLine`/`checkout.promo_lines_for`
(mostrador y mesa) y `cart.service._cart_promo_lines` (carrito del comensal).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_promotions_toppings_base_price -v
"""
from decimal import Decimal
import unittest
import uuid

from app.characterization_tests import cart_fixtures as fx
from app.api.v1.sales.builder import SaleLine
from app.api.v1.orders import checkout
from app.api.v1.cart import service as cart_service
from app.models.cart_item import CartItemOption


class TestSaleLineBaseUnitPrice(unittest.TestCase):
    """`SaleLine.base_unit_price`: `unit_price` menos la suma de
    `extra_price * quantity` de cada opción elegida (mostrador y mesa)."""

    def test_sin_opciones_base_igual_a_unit_price(self):
        line = SaleLine(
            product_variant_id=uuid.uuid4(), description="malteada",
            options=[], quantity=1, unit_price=Decimal("8000"),
        )
        self.assertEqual(line.base_unit_price, Decimal("8000"))

    def test_con_toppings_resta_el_extra_price_por_cantidad(self):
        line = SaleLine(
            product_variant_id=uuid.uuid4(), description="malteada con toppings",
            options=[
                {"option_id": str(uuid.uuid4()), "name": "chispas", "extra_price": "1000", "quantity": 1},
                {"option_id": str(uuid.uuid4()), "name": "banano", "extra_price": "500", "quantity": 2},
            ],
            quantity=1, unit_price=Decimal("10000"),  # 8000 base + 1000 + 500*2
        )
        self.assertEqual(line.base_unit_price, Decimal("8000"))


class TestPromoLinesForBaseUnitPrice(unittest.TestCase):
    """`checkout.promo_lines_for` (mostrador/mesa) propaga `base_unit_price`."""

    def setUp(self):
        self.db = fx.new_session()
        self.prod = fx.make_product(self.db)
        self.variant = fx.make_variant(self.db, product=self.prod, price=Decimal("8000"))
        self.db.commit()

    def test_base_unit_price_viaja_al_dict_de_promo_lines(self):
        line = SaleLine(
            product_variant_id=self.variant.id, description="malteada con toppings",
            options=[{"option_id": str(uuid.uuid4()), "name": "chispas",
                      "extra_price": "1500", "quantity": 1}],
            quantity=1, unit_price=Decimal("9500"),
        )
        [promo_line] = checkout.promo_lines_for(self.db, [line])
        self.assertEqual(promo_line["unit_price"], Decimal("9500"))
        self.assertEqual(promo_line["base_unit_price"], Decimal("8000"))


class TestCartPromoLinesBaseUnitPrice(unittest.TestCase):
    """`cart.service._cart_promo_lines` (carrito del comensal) resuelve
    `base_unit_price` restando el `extra_price` de las `CartItemOption`
    elegidas -- consulta viva a `Option`, igual criterio que `SaleLine`."""

    def setUp(self):
        self.db = fx.new_session()
        self.prod = fx.make_product(self.db)
        self.variant = fx.make_variant(self.db, product=self.prod, price=Decimal("8000"))
        self.group = fx.make_option_group(self.db)
        self.topping = fx.make_option(self.db, group=self.group, extra_price=Decimal("1500"))
        self.db.commit()

    def test_item_con_topping_resta_extra_price(self):
        cart = fx.make_cart(self.db)
        item = fx.make_cart_item(
            self.db, cart, self.variant, unit_price=Decimal("9500"),
        )
        self.db.add(CartItemOption(cart_item_id=item.id, option_id=self.topping.id, quantity=1))
        self.db.commit()
        self.db.refresh(cart)

        [promo_line] = cart_service._cart_promo_lines(self.db, cart)
        self.assertEqual(promo_line["unit_price"], Decimal("9500"))
        self.assertEqual(promo_line["base_unit_price"], Decimal("8000"))

    def test_item_sin_opciones_base_igual_a_unit_price(self):
        cart = fx.make_cart(self.db)
        fx.make_cart_item(self.db, cart, self.variant, unit_price=Decimal("8000"))
        self.db.commit()
        self.db.refresh(cart)

        [promo_line] = cart_service._cart_promo_lines(self.db, cart)
        self.assertEqual(promo_line["base_unit_price"], Decimal("8000"))


if __name__ == "__main__":
    unittest.main()
