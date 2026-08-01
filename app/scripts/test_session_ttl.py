"""Test del refresco deslizante perezoso y su interacción con el barrido.

No hay pytest en el proyecto, así que es un script autoejecutable:

    python -m app.scripts.test_session_ttl

No toca la base de datos: `_should_refresh` y la derivación del barrido son
aritmética pura sobre `expires_at`, y eso es justo lo que hay que blindar.

El test que importa es `test_no_rompe_el_barrido`: la holgura del refresco es el
error máximo con el que el barrido reconstruye la última actividad
(`scheduler.py:64`), así que si supera `EMPTY_SESSION_TTL_MINUTES` el barrido
empieza a cerrar mesas activas.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import settings
from app.core.qr_context import _should_refresh


@dataclass
class _FakeParticipant:
    """Lo único que mira `_should_refresh`."""
    expires_at: Optional[datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _con_ventana_restante(now: datetime, minutos: float) -> _FakeParticipant:
    return _FakeParticipant(expires_at=now + timedelta(minutes=minutos))


def test_invariante_de_configuracion():
    """La holgura DEBE ser menor que la ventana del barrido de mesas sin pedir."""
    assert settings.SESSION_TTL_REFRESH_SLACK_MINUTES < settings.EMPTY_SESSION_TTL_MINUTES, (
        f"SESSION_TTL_REFRESH_SLACK_MINUTES={settings.SESSION_TTL_REFRESH_SLACK_MINUTES} "
        f"debe ser < EMPTY_SESSION_TTL_MINUTES={settings.EMPTY_SESSION_TTL_MINUTES}; "
        "si no, el barrido cierra sesiones activas sin pedidos"
    )
    assert settings.SESSION_TTL_REFRESH_SLACK_MINUTES > 0, "una holgura de 0 es el bug original"
    print("  ok  · holgura < ventana del barrido, y > 0")


def test_tabla_de_verdad():
    now = _now()
    ventana = settings.SESSION_TTL_MINUTES
    holgura = settings.SESSION_TTL_REFRESH_SLACK_MINUTES

    casos = [
        # (minutos que le quedan a la ventana, ¿debe refrescar?, etiqueta)
        (ventana, False, "recién refrescado → no se reescribe"),
        (ventana - holgura / 2, False, "dentro de la holgura → no se reescribe"),
        (ventana - holgura, True, "justo en el borde de la holgura → se reescribe"),
        (ventana - holgura - 1, True, "pasada la holgura → se reescribe"),
        (1, True, "casi vencido → se reescribe"),
    ]
    for restante, esperado, label in casos:
        got = _should_refresh(_con_ventana_restante(now, restante), now)
        assert got is esperado, f"{label}: esperaba {esperado}, vino {got}"
        print(f"  ok  · {label}")

    assert _should_refresh(_FakeParticipant(expires_at=None), now) is True
    print("  ok  · expires_at None → se reescribe")


def test_ahorro_de_escrituras():
    """Un comensal sondeando cada 10 s durante una hora: cuántos UPDATE hace."""
    now = _now()
    p = _con_ventana_restante(now, settings.SESSION_TTL_MINUTES)
    escrituras = 0
    for tick in range(0, 3600, 10):
        t = now + timedelta(seconds=tick)
        if _should_refresh(p, t):
            p.expires_at = t + timedelta(minutes=settings.SESSION_TTL_MINUTES)
            escrituras += 1

    esperado = 3600 // (settings.SESSION_TTL_REFRESH_SLACK_MINUTES * 60)
    assert escrituras <= esperado + 1, f"{escrituras} escrituras/h, esperaba ~{esperado}"
    print(f"  ok  · {escrituras} escrituras/h en vez de 360 (sondeo a 10 s)")


def test_no_rompe_el_barrido():
    """Una sesión activa nunca debe parecer abandonada por culpa de la holgura.

    Réplica de la aritmética de `_abandonadas_sin_pedir` (scheduler.py:62-66):
    el barrido considera abandonada la sesión cuando
    `expires_at - SESSION_TTL_MINUTES <= now - EMPTY_SESSION_TTL_MINUTES`.
    """
    now = _now()
    p = _con_ventana_restante(now, settings.SESSION_TTL_MINUTES)

    # Comensal activo: sondea cada 10 s durante el doble de la ventana del barrido.
    horizonte = settings.EMPTY_SESSION_TTL_MINUTES * 2 * 60
    for tick in range(0, horizonte, 10):
        t = now + timedelta(seconds=tick)
        if _should_refresh(p, t):
            p.expires_at = t + timedelta(minutes=settings.SESSION_TTL_MINUTES)

        ultima_actividad = p.expires_at - timedelta(minutes=settings.SESSION_TTL_MINUTES)
        limite = t - timedelta(minutes=settings.EMPTY_SESSION_TTL_MINUTES)
        assert ultima_actividad > limite, (
            f"a los {tick}s el barrido habría cerrado una mesa activa: "
            f"última actividad derivada {ultima_actividad} <= límite {limite}"
        )
    print(f"  ok  · sesión activa no cae en el barrido tras {horizonte // 60} min")

    # Y al revés: una sesión de verdad abandonada sí tiene que caer.
    p_muerto = _con_ventana_restante(now, settings.SESSION_TTL_MINUTES)
    t = now + timedelta(minutes=settings.EMPTY_SESSION_TTL_MINUTES + 1)
    ultima_actividad = p_muerto.expires_at - timedelta(minutes=settings.SESSION_TTL_MINUTES)
    limite = t - timedelta(minutes=settings.EMPTY_SESSION_TTL_MINUTES)
    assert ultima_actividad <= limite, "una sesión abandonada debe seguir cayendo en el barrido"
    print("  ok  · sesión abandonada sí cae en el barrido")


def main():
    print("Refresco deslizante perezoso")
    test_invariante_de_configuracion()
    test_tabla_de_verdad()
    test_ahorro_de_escrituras()
    test_no_rompe_el_barrido()
    print("TODO OK ✔")


if __name__ == "__main__":
    main()
