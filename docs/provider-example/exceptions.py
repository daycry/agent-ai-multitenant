class LLMError(Exception):
    """Error genérico de la capa."""


class AuthError(LLMError):
    """Fallo de autenticación o token expirado/revocado."""


class RateLimitError(LLMError):
    """Rate limit del proveedor."""


class ProviderError(LLMError):
    """Error devuelto por el proveedor."""

    def __init__(self, message: str, status_code: int | None = None, raw: object = None):
        super().__init__(message)
        self.status_code = status_code
        self.raw = raw
