"""Marca como inactivas las promociones vencidas, en todos los tenants.

    python -m app.scripts.expire_promotions

Hace lo mismo que el job periódico de medianoche del `lifespan`, pero a demanda.
Es puramente informativo: `_valid_now()` ya evalúa `ends_at` en tiempo real, así
que las promociones vigentes no dependen de que este script haya corrido.
"""
import logging

from app.core.scheduler import expire_promotions


def main() -> None:
    logging.basicConfig(level=logging.INFO, force=True)
    print("Expirando promociones vencidas…")
    n = expire_promotions()
    print(f"Promociones expiradas: {n}")


if __name__ == "__main__":
    main()
