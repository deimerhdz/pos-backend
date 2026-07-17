"""Test del contrato de tokens de QR/sesión (Fase 0).

No hay pytest en el proyecto, así que es un script autoejecutable:

    python -m app.scripts.test_qr_token

Cubre: roundtrip mint→verify de ambos tokens, detección de manipulación,
expiración del token de sesión, y aislamiento respecto a los tokens de auth.
"""
from datetime import timedelta
from uuid import uuid4

import jwt

from app.core.config import settings
from app.core.qr_token import (
    QrTokenError,
    SessionExpiredError,
    SessionInvalidError,
    mint_qr_token,
    mint_session_token,
    verify_qr_token,
    verify_session_token,
)
from app.core.utils import create_access_token


def _expect(exc_type, fn, label):
    try:
        fn()
    except exc_type:
        print(f"  ok  · {label}")
        return
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"{label}: se esperaba {exc_type.__name__}, vino {type(e).__name__}: {e}")
    raise AssertionError(f"{label}: se esperaba {exc_type.__name__}, no se lanzó nada")


def test_qr_roundtrip():
    tid, tbl = 7, uuid4()
    claims = verify_qr_token(mint_qr_token(tid, tbl))
    assert claims.tenant_id == tid, claims
    assert claims.table_id == tbl, claims
    print("  ok  · QR roundtrip (tenant_id + table_id)")


def test_session_roundtrip():
    tid, tbl, sid = 7, uuid4(), uuid4()
    claims = verify_session_token(mint_session_token(tid, tbl, sid))
    assert claims.tenant_id == tid, claims
    assert claims.table_id == tbl, claims
    assert claims.session_id == sid, claims
    print("  ok  · sesión roundtrip (tenant + mesa + session_id)")


def test_tamper_detection():
    good = mint_qr_token(1, uuid4())
    tampered = good[:-2] + ("aa" if good[-2:] != "aa" else "bb")
    _expect(QrTokenError, lambda: verify_qr_token(tampered), "QR manipulado → QrTokenError")

    bad_secret = jwt.encode(
        {"typ": "qr", "t": 1, "tb": str(uuid4())}, "otro-secreto", algorithm=settings.JWT_ALGORITHM
    )
    _expect(QrTokenError, lambda: verify_qr_token(bad_secret), "QR firmado con otro secreto → QrTokenError")


def test_session_expiry():
    tid, tbl, sid = 1, uuid4(), uuid4()
    expired = mint_session_token(tid, tbl, sid, ttl_minutes=-1)
    _expect(SessionExpiredError, lambda: verify_session_token(expired), "sesión expirada → SessionExpiredError")


def test_type_isolation():
    # Un token de auth (con user/refresh) no puede pasar como token de mesa.
    auth = create_access_token({"email": "a@b.c", "uid": str(uuid4())})
    _expect(QrTokenError, lambda: verify_qr_token(auth), "auth token → verify_qr_token lo rechaza")
    _expect(SessionInvalidError, lambda: verify_session_token(auth), "auth token → verify_session_token lo rechaza")

    # Un token de QR no puede pasar como token de sesión y viceversa.
    qr = mint_qr_token(1, uuid4())
    _expect(SessionInvalidError, lambda: verify_session_token(qr), "QR token → verify_session_token lo rechaza")
    sess = mint_session_token(1, uuid4(), uuid4())
    _expect(QrTokenError, lambda: verify_qr_token(sess), "sesión token → verify_qr_token lo rechaza")


def main():
    print("Fase 0 · contrato de tokens de QR/sesión")
    test_qr_roundtrip()
    test_session_roundtrip()
    test_tamper_detection()
    test_session_expiry()
    test_type_isolation()
    print("TODO OK ✔")


if __name__ == "__main__":
    main()
