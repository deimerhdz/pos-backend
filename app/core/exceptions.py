class TenantNotFoundError(Exception):
    """Se lanza cuando no se encuentra el tenant."""

    def __init__(self, host: str):
        self.host = host
        super().__init__(f"Tenant not found for host '{host}'")
        

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
