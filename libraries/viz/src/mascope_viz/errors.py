"""What mascope_viz raises: failures that say what to do next.

Every failure is a :class:`VizError` whose message says what happened and
what to do about it, and whose :meth:`~VizError.to_dict` gives the same as
plain JSON values, for the CLI and an agent to hand back in place of a stack
trace. The SDK's exceptions are mapped onto these by :func:`sdk_errors`; the
SDK has already retried what was transient.

An ambiguous name is not among them: a scope that names several objects
resolves to their candidates (:attr:`ResolvedScope.candidates
<mascope_viz.data.scope.ResolvedScope.candidates>`), as the AI layer's
``find`` answers with them.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from mascope_sdk.exceptions import (
    AuthenticationError,
    MascopeAPIError,
    MascopeConnectionError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)


#: How many names a message lists before it says how many more there are.
LISTED = 20


def listed(names: Sequence[str]) -> str:
    """*names* for a message: the first :data:`LISTED` of them, quoted."""
    shown = ", ".join(repr(name) for name in names[:LISTED])
    more = len(names) - LISTED
    return shown + (f" and {more} more" if more > 0 else "")


class VizError(Exception):
    """A failure the caller can act on.

    :ivar code: What kind of failure, as ``to_dict()["error"]`` says it.
    :ivar message: What happened and what to do next.
    """

    code = "error"

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)

    def details(self) -> dict[str, Any]:
        """The failure's particulars, as plain JSON values."""
        return {}

    def to_dict(self) -> dict[str, Any]:
        """The failure as plain JSON values: ``error``, ``message`` and its
        particulars."""
        return {"error": self.code, "message": self.message, **self.details()}


class InvalidScope(VizError, ValueError):
    """A scope that cannot mean anything as written: fix its arguments.

    A :class:`ValueError` too, which is what a constructor refusing its
    arguments raises.
    """

    code = "invalid_scope"


class NotFound(VizError):
    """A name or ID that matches nothing the user can see.

    :ivar query: The name or ID asked for.
    :ivar kind: What it was to name: ``dataset``, ``batch``, ...
    :ivar available: The names there are to choose from.
    """

    code = "not_found"

    def __init__(self, query: str, kind: str, available: Sequence[str]):
        self.query = query
        self.kind = kind
        self.available = list(available)
        there = (
            f"The {kind}s there are: {listed(self.available)}."
            if self.available
            else f"This user can see no {kind} at all."
        )
        super().__init__(f"No {kind} matching {query!r}. {there}")

    def details(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "kind": self.kind,
            "n_available": len(self.available),
            "available": self.available[:LISTED],
        }


class AccessDenied(VizError):
    """A 403: the token's user may not read what the scope names."""

    code = "access_denied"


class NotAuthenticated(VizError):
    """A 401: the token is missing, expired, or for another service name."""

    code = "not_authenticated"


class Unavailable(VizError):
    """The server could not be reached or failed, after the SDK's retries.

    Transient by nature: the same call may succeed later.
    """

    code = "unavailable"


@contextmanager
def sdk_errors(operation: str) -> Iterator[None]:
    """Raise the SDK's exceptions inside as :class:`VizError`.

    :param operation: What the calls inside do, for the message: ``"list
        the batches of dataset 'd1'"``.
    """
    try:
        yield
    except VizError:
        raise
    except PermissionDeniedError as e:
        raise AccessDenied(
            f"Refused to {operation}: this user needs at least guest access "
            "to it. Ask a workspace admin for the role, or scope the digest "
            "to data you can read."
        ) from e
    except AuthenticationError as e:
        raise NotAuthenticated(
            "The server refused the token (HTTP 401): it is missing, has "
            "expired, or was generated for another service than the one the "
            "client names. Generate a new token in Mascope's settings."
        ) from e
    except NotFoundError as e:
        raise Unavailable(
            f"Could not {operation}: the server has no such object (HTTP "
            "404). It may have been deleted since it was listed; resolve "
            "the scope again."
        ) from e
    except ValidationError as e:
        raise InvalidScope(f"The server refused to {operation}: {e.message}") from e
    except (MascopeAPIError, MascopeConnectionError) as e:
        raise Unavailable(
            f"Could not {operation}: {e}. The SDK already retried; try "
            "again later, or ask whoever runs the server."
        ) from e
