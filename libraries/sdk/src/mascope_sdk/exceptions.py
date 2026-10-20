"""Custom exceptions for the Mascope SDK."""


class MascopeError(Exception):
    """Base exception for all Mascope SDK errors."""

    pass


class ConfirmationRequiredError(MascopeError):
    """Raised when a load needs confirming and nobody is there to confirm it.

    A loader whose ``confirm_above`` threshold is exceeded asks on stdin
    before it starts; a script, a pipeline or an agent has no stdin to answer
    from, and is stopped here rather than waved through. Pass
    ``confirm_above=None`` (or a higher threshold) to load without asking.

    :ivar count: The number of samples the load would read.
    :vartype count: int
    :ivar threshold: The ``confirm_above`` threshold it exceeded.
    :vartype threshold: int
    """

    def __init__(self, count: int, threshold: int):
        self.count = count
        self.threshold = threshold
        super().__init__(
            f"About to load data for {count} samples, above the confirmation "
            f"threshold of {threshold}, and there is no interactive input to "
            "confirm it. Pass confirm_above=None to load without asking, or "
            "narrow the selection."
        )


class ConfigurationError(MascopeError):
    """Raised when SDK configuration is invalid or missing.

    This includes missing environment variables, invalid URLs, or missing credentials.
    """

    pass


class MascopeAPIError(MascopeError):
    """Base exception for API-related errors.

    :ivar status_code: HTTP status code from the API response.
    :vartype status_code: int | None
    :ivar message: Error message from the API or a default message.
    :vartype message: str
    :ivar url: The URL that was being accessed when the error occurred.
    :vartype url: str | None
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        url: str | None = None,
    ):
        self.status_code = status_code
        self.message = message
        self.url = url
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        parts = [self.message]
        if self.status_code:
            parts.insert(0, f"[HTTP {self.status_code}]")
        if self.url:
            parts.append(f"(URL: {self.url})")
        return " ".join(parts)


class AuthenticationError(MascopeAPIError):
    """Raised when authentication fails (401/403 responses).

    This typically means the access token is invalid, expired, or missing.
    """

    pass


class PermissionDeniedError(AuthenticationError):
    """Raised when the token is valid but its user lacks the role (403).

    The server answers a refused write with a bare 403, naming neither the
    object nor the role. A write method of the SDK knows both - what it acted
    on and the role its route asks for, as the SDK docs list them - and says
    so here. On a read, or a write that does not say, they are None.

    A subclass of :class:`AuthenticationError`, which a 403 raised before
    this class existed, so an ``except AuthenticationError`` still catches it.

    :ivar operation: What the call set out to do, e.g. ``"move dataset"``.
    :vartype operation: str | None
    :ivar object_type: The kind of object it acted on, e.g. ``"dataset"``.
    :vartype object_type: str | None
    :ivar object_id: That object's ID.
    :vartype object_id: str | None
    :ivar required_role: The role the operation needs, and where.
    :vartype required_role: str | None
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = 403,
        url: str | None = None,
        *,
        operation: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        required_role: str | None = None,
    ):
        self.operation = operation
        self.object_type = object_type
        self.object_id = object_id
        self.required_role = required_role
        super().__init__(message, status_code=status_code, url=url)

    def _format_message(self) -> str:
        if self.operation is None:
            return super()._format_message()
        target = f"{self.object_type} '{self.object_id}'"
        parts = [
            f"[HTTP {self.status_code}]" if self.status_code else "",
            f"Refused to {self.operation} {target}:",
            f"it needs {self.required_role}.",
            f"The server said: {self.message}",
        ]
        if self.url:
            parts.append(f"(URL: {self.url})")
        return " ".join(part for part in parts if part)


class NotFoundError(MascopeAPIError):
    """Raised when a requested resource is not found (404 response)."""

    pass


class ValidationError(MascopeAPIError):
    """Raised when request validation fails (422 response).

    This typically means the request parameters were invalid.
    """

    pass


class ServerError(MascopeAPIError):
    """Raised when the server encounters an error (5xx responses)."""

    pass


class TusNotSupportedError(MascopeAPIError):
    """Raised when the server lacks an endpoint the agent asked for.

    Now raised only by device-token renewal, on a 404 from a server that
    predates it: the agent keeps its current token and backs off instead of
    treating the absence as a credential failure. Uploads no longer raise
    it - every supported server accepts agent TUS uploads, so a refusal
    there is a real error and keeps its own type.

    The name outlived its original meaning and is kept only to avoid churn
    across the SDK, the agent and their tests; it is not a top-level export
    (`mascope_sdk.__all__` omits it), so renaming it costs nothing beyond
    those call sites.
    """

    pass


class MascopeConnectionError(MascopeError):
    """Raised when unable to connect to the Mascope server.

    This includes network errors, DNS failures, and timeouts.
    """

    def __init__(self, message: str, url: str | None = None):
        self.url = url
        super().__init__(f"{message}" + (f" (URL: {url})" if url else ""))


class MascopeTimeoutError(MascopeConnectionError):
    """Raised when a request times out."""

    pass
