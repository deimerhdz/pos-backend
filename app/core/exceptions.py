from fastapi import HTTPException, status


class TenantNotFoundError(Exception):
    """Se lanza cuando no se encuentra el tenant."""

    def __init__(self, host: str):
        self.host = host
        super().__init__(f"Tenant not found for host '{host}'")


class InsufficientStockError(HTTPException):
    """Un insumo no tiene stock vigente (no vencido) suficiente para el consumo.

    Es HTTPException(400) para que FastAPI la traduzca sola y para que los
    `except HTTPException` que envuelven la transacción hagan rollback y re-lancen.
    """

    def __init__(self, supply_name: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stock insuficiente o vencido para el insumo '{supply_name}'.",
        )
        

class InvalidToken(Exception):
    """User has provided an invalid or expired token"""

    pass


class RevokedToken(Exception):
    """User has provided a token that has been revoked"""

    pass


class AccessTokenRequired(Exception):
    """User has provided a refresh token when an access token is needed"""

    pass


class RefreshTokenRequired(Exception):
    """User has provided an access token when a refresh token is needed"""

    pass


class UserNotFound(Exception):
    """User Not found"""

    pass
