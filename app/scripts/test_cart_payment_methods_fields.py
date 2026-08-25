"""Test de `GET /cart/payment-methods` con el campo `fields` (spec 034, US3).

No hay pytest en el proyecto, así que es un script autoejecutable:

    python -m app.scripts.test_cart_payment_methods_fields

No toca la base de datos real: usa el mismo fixture SQLite en memoria que los
characterization tests de spec 032
(`app/characterization_tests/payment_catalog_fixtures.py`) para poblar
`payment_method_catalog` (esquema `shared`) + `payment_methods` (esquema
`tenant`) y ejercer `cart/service.list_payment_methods` +
`cart/schemas.DinerPaymentMethod` tal como los usa el router.

Cubre: el `format` de cada campo del catálogo llega intacto al comensal
(FR-011/FR-012), un método sin `catalog_id` (p. ej. Efectivo) no revienta y
expone `fields=[]` (FR-013), y un método desactivado nunca aparece (FR-004,
comportamiento ya vigente que este cambio no debe romper).
"""
from app.characterization_tests import payment_catalog_fixtures as fx
from app.api.v1.cart import service as cart_service
from app.api.v1.cart.schemas import DinerPaymentMethod


def _by_name(methods, name):
    return next(m for m in methods if m.name == name)


def test_fields_llega_con_el_format_correcto():
    db = fx.new_session()
    catalog = fx.make_payment_method_catalog(
        db, name="Nequi", type="transfer",
        fields=[
            {"key": "celular", "label": "Celular", "required": True, "format": "numeric", "length": 10},
            {"key": "qr", "label": "Código QR", "required": False, "format": "image"},
        ],
    )
    fx.make_payment_method(
        db, catalog_id=catalog.id, name="Nequi", type="transfer", is_cash=False,
        payment_info={"celular": "3001234567", "qr": "https://cdn.example.com/qr-nequi.png"},
    )
    db.commit()

    metodo = _by_name(cart_service.list_payment_methods(db), "Nequi")
    out = DinerPaymentMethod.model_validate(metodo)

    por_key = {f["key"]: f for f in out.fields}
    assert por_key["celular"]["format"] == "numeric", por_key
    assert por_key["qr"]["format"] == "image", por_key
    assert por_key["celular"]["required"] is True, por_key
    assert por_key["qr"]["required"] is False, por_key
    print("  ok  · fields expone el format correcto por clave (numeric/image)")


def test_sin_catalogo_no_revienta_y_fields_queda_vacio():
    db = fx.new_session()
    fx.make_payment_method(db, catalog_id=None, name="Efectivo", is_cash=True)
    db.commit()

    metodo = _by_name(cart_service.list_payment_methods(db), "Efectivo")
    out = DinerPaymentMethod.model_validate(metodo)

    assert out.fields == [], out.fields
    print("  ok  · método sin catalog_id (ej. Efectivo) expone fields=[] sin error")


def test_metodo_desactivado_no_aparece():
    """No-regresión (FR-004): agregar `fields` no debe tocar el filtro `active`."""
    db = fx.new_session()
    catalog = fx.make_payment_method_catalog(
        db, name="Daviplata", type="transfer",
        fields=[{"key": "celular", "label": "Celular", "required": True, "format": "numeric"}],
    )
    fx.make_payment_method(
        db, catalog_id=catalog.id, name="Daviplata", type="transfer", is_cash=False, active=False,
    )
    db.commit()

    nombres = {m.name for m in cart_service.list_payment_methods(db)}
    assert "Daviplata" not in nombres, nombres
    print("  ok  · un método desactivado sigue sin aparecer")


def main():
    print("GET /cart/payment-methods — campo `fields` (spec 034)")
    test_fields_llega_con_el_format_correcto()
    test_sin_catalogo_no_revienta_y_fields_queda_vacio()
    test_metodo_desactivado_no_aparece()
    print("TODO OK ✔")


if __name__ == "__main__":
    main()
