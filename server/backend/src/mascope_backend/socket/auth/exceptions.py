"""Socket authentication exceptions."""


class SocketAuthError(Exception):
    """Base exception for socket authentication errors."""

    def __init__(self, message: str, error_code: str):
        """
        :param message: Human-readable error message
        :type message: str
        :param error_code: Machine-readable error code
        :type error_code: str
        """
        self.message = message
        self.error_code = error_code
        super().__init__(message)


class SocketUnauthenticatedError(SocketAuthError):
    """Raised when socket authentication fails."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message=message, error_code="SOCKET_UNAUTHENTICATED")


class SocketForbiddenError(SocketAuthError):
    """Raised when a socket client lacks required permissions."""

    def __init__(self, message: str = "Insufficient permissions for this action."):
        super().__init__(message=message, error_code="SOCKET_FORBIDDEN")


class SocketAuthConfigError(SocketAuthError):
    """Raised when there are authentication configuration issues."""

    def __init__(
        self, message: str = "The required role is not defined in the configuration."
    ):
        super().__init__(message=message, error_code="SOCKET_AUTH_CONFIG_ERROR")


class SocketAuthUnavailableError(SocketAuthError):
    """Raised when a token could not be checked, rather than found bad."""

    def __init__(
        self, message: str = "The access token could not be validated just now."
    ):
        super().__init__(message=message, error_code="SOCKET_AUTH_UNAVAILABLE")
