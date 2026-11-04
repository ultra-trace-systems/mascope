"""Custom exceptions for the Mascope SDK."""

from typing import Any


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

    The server answers an error with ``{"error": <for a person>, "detail":
    {...}}``. ``message`` is the first; ``detail`` is the second, which is
    where the server puts what a program reacts to: an ``error_id`` to quote
    when reporting the error, a stable ``code`` where a client is expected to
    tell one condition from another that shares its status, and the ids the
    condition is about. Branch on ``code``, not on the wording of ``message``.

    :ivar status_code: HTTP status code from the API response.
    :vartype status_code: int | None
    :ivar message: Error message from the API or a default message.
    :vartype message: str
    :ivar url: The URL that was being accessed when the error occurred.
    :vartype url: str | None
    :ivar body: The response body as parsed JSON, or None when it was not
        JSON.
    :vartype body: Any
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        url: str | None = None,
        *,
        body: Any = None,
    ):
        self.status_code = status_code
        self.message = message
        self.url = url
        self.body = body
        super().__init__(self._format_message())

    @property
    def detail(self) -> dict[str, Any]:
        """The body's ``detail`` object; empty when it sent none."""
        detail = self.body.get("detail") if isinstance(self.body, dict) else None
        return detail if isinstance(detail, dict) else {}

    @property
    def code(self) -> str | None:
        """The stable code of the condition, where the server names one."""
        code = self.detail.get("code")
        return code if isinstance(code, str) else None

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
        body: Any = None,
        operation: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        required_role: str | None = None,
    ):
        self.operation = operation
        self.object_type = object_type
        self.object_id = object_id
        self.required_role = required_role
        super().__init__(message, status_code=status_code, url=url, body=body)

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


class ConflictError(MascopeAPIError):
    """Raised when the server refuses a request over the state it is in (409).

    Sending it again unchanged gets the same answer until that state changes:
    the server's ``detail`` says what holds it (see :attr:`detail`).
    """

    pass


class ServerError(MascopeAPIError):
    """Raised when the server encounters an error (5xx responses)."""

    pass


class AssignmentInFlightError(ConflictError):
    """Raised when a sample already has a peak assignment run in flight.

    The server admits one run per sample at a time and names the one it is
    running, so the caller can follow that run instead
    (``peak_assignments.wait_for_run``) rather than start another.

    :ivar sample_id: The sample.
    :vartype sample_id: str | None
    :ivar peak_assignment_run_id: The run in flight. None when the server
        could not name it: another request was creating it at that moment.
    :vartype peak_assignment_run_id: str | None
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = 409,
        url: str | None = None,
        *,
        body: Any = None,
    ):
        super().__init__(message, status_code=status_code, url=url, body=body)
        self.sample_id = self.detail.get("sample_item_id")
        self.peak_assignment_run_id = self.detail.get("peak_assignment_run_id")


class SampleIneligibleError(ValidationError):
    """Raised when a sample cannot usefully be assigned peaks (422).

    A blank sample has no peaks, and a sample whose m/z calibration is not
    verified would give mass errors, and so tiers, that mean nothing. No run
    is created. A subclass of :class:`ValidationError`, the type of every
    other 422, and told apart from a request the server could not read by
    the code ``sample_ineligible``.

    :ivar sample_id: The sample.
    :vartype sample_id: str | None
    :ivar reason: Why it cannot be assigned, as the server says it.
    :vartype reason: str | None
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = 422,
        url: str | None = None,
        *,
        body: Any = None,
    ):
        super().__init__(message, status_code=status_code, url=url, body=body)
        self.sample_id = self.detail.get("sample_item_id")
        self.reason = self.detail.get("reason")


class ImportValidationError(MascopeError, ValueError):
    """Raised when ``peak_assignments.import_run`` refuses a frame itself.

    Nothing has been sent: the frame, or an argument beside it, is one the
    server would refuse, or one that cannot be sent at all - a NaN in a
    column that may not be null, a value no JSON can carry. A subclass of
    :class:`ValueError` too, as the other checks a call makes before it
    sends anything raise.

    :ivar column: The column at fault, or None when the fault is not one
        column's.
    :vartype column: str | None
    :ivar rows: The index labels of the rows at fault, empty when the
        fault is not a row's.
    :vartype rows: list
    """

    def __init__(
        self, message: str, *, column: str | None = None, rows: list | None = None
    ):
        self.column = column
        self.rows = list(rows or [])
        super().__init__(message)


class AssignmentRunError(MascopeError):
    """Raised when a peak assignment run ended without a ledger.

    A run that failed carries the server's reason in :attr:`error`. A
    cancelled run wrote no rows at all, so it is an error too, rather than an
    empty result that would read as a sample with nothing in it.

    :ivar sample_id: The sample.
    :vartype sample_id: str
    :ivar peak_assignment_run_id: The run.
    :vartype peak_assignment_run_id: str
    :ivar status: ``failed`` or ``cancelled``; None when the run is no longer
        listed at all.
    :vartype status: str | None
    :ivar error: The run's own failure reason, if it recorded one.
    :vartype error: str | None
    """

    def __init__(
        self,
        sample_id: str,
        peak_assignment_run_id: str,
        status: str | None,
        error: str | None = None,
    ):
        self.sample_id = sample_id
        self.peak_assignment_run_id = peak_assignment_run_id
        self.status = status
        self.error = error
        if status is None:
            text = "is no longer listed for the sample"
        else:
            text = f"ended {status}"
        message = f"Peak assignment run '{peak_assignment_run_id}' {text}"
        super().__init__(f"{message}: {error}" if error else f"{message}.")


class AssignmentTimeoutError(MascopeError):
    """Raised when a peak assignment run is still going when the wait ends.

    The run is not lost and not stopped: it carries on on the server. Its id
    is here, so the caller can wait again
    (``peak_assignments.wait_for_run``) or read it later.

    :ivar sample_id: The sample.
    :vartype sample_id: str
    :ivar peak_assignment_run_id: The run.
    :vartype peak_assignment_run_id: str
    :ivar status: The status it was last seen in; None if no poll was
        answered.
    :vartype status: str | None
    :ivar timeout: How long the wait was, in seconds.
    :vartype timeout: float
    """

    def __init__(
        self,
        sample_id: str,
        peak_assignment_run_id: str,
        status: str | None,
        timeout: float,
    ):
        self.sample_id = sample_id
        self.peak_assignment_run_id = peak_assignment_run_id
        self.status = status
        self.timeout = timeout
        seen = f"still {status}" if status else "not seen in a listing"
        super().__init__(
            f"Peak assignment run '{peak_assignment_run_id}' of sample "
            f"'{sample_id}' is {seen} after {timeout:g} s. It carries on on "
            "the server: wait_for_run(sample_id, peak_assignment_run_id) "
            "waits again."
        )


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
        self.message = message
        self.url = url
        super().__init__(f"{message}" + (f" (URL: {url})" if url else ""))


class MascopeTimeoutError(MascopeConnectionError):
    """Raised when a request times out."""

    pass
