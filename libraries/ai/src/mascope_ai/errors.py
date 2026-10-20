"""What the toolkit raises: structured results a model can act on.

What a model sees when a call fails decides whether it recovers or makes
something up. So every failure is a :class:`ToolkitError` whose message says
what happened and what to do next, and whose :meth:`~ToolkitError.to_dict`
gives the same as plain JSON values - an ``error`` code and the details - for
a transport to hand back in place of a stack trace. Long lists (candidates,
the samples a load left) are cut to :data:`LISTED` entries there, with the
full count beside them.

The SDK's exceptions are mapped onto these by :func:`sdk_errors`. Transient
failures were already retried by the SDK (four attempts, exponential
backoff); the toolkit adds no retry of its own.
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
    ServerError,
    ValidationError,
)

from .refs import Ref


#: How many entries of a long list a message and ``to_dict`` show.
LISTED = 20


class ToolkitError(Exception):
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


def _listed(items: Sequence[Any]) -> list[Any]:
    return [item.to_dict() if isinstance(item, Ref) else item for item in items][
        :LISTED
    ]


class AmbiguousName(ToolkitError):
    """A name that matches more than one object.

    Not an error to an agent so much as an answer: the candidates are on it,
    and the call goes through with one of them.

    :ivar query: The name asked for.
    :ivar kind: The kind of object asked for, if one was.
    :ivar candidates: Every object it matches.
    """

    code = "ambiguous"

    def __init__(self, query: str, kind: str | None, candidates: Sequence[Ref]):
        self.query = query
        self.kind = kind
        self.candidates = list(candidates)
        shown = ", ".join(str(ref) for ref in self.candidates[:LISTED])
        more = len(self.candidates) - LISTED
        super().__init__(
            f"{query!r} matches {len(self.candidates)} "
            f"{kind + 's' if kind else 'objects'}: {shown}"
            f"{f' and {more} more' if more > 0 else ''}. "
            "Pass one of them (its Ref or its ID), or a more specific name."
        )

    def details(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "kind": self.kind,
            "n_candidates": len(self.candidates),
            "candidates": _listed(self.candidates),
        }


class NotFound(ToolkitError):
    """No object by that name or ID, or none the user may see.

    :ivar query: The name or ID asked for.
    :ivar kind: The kind of object asked for, if one was.
    """

    code = "not_found"

    def __init__(self, query: str, kind: str | None, hint: str = ""):
        self.query = query
        self.kind = kind
        super().__init__(
            f"No {kind or 'object'} matching {query!r} that this user can see."
            f"{' ' + hint if hint else ''}"
        )

    def details(self) -> dict[str, Any]:
        return {"query": self.query, "kind": self.kind}


class RowCapExceeded(ToolkitError):
    """A load that would pass its ``max_rows`` cap, stopped before it did.

    Raised either before the load, from an estimate that is already over the
    cap, or during it, as soon as the rows read pass it. No Frame is made
    either way.

    :ivar rows: The rows read when the load stopped, or the estimate.
    :ivar cap: The ``max_rows`` it passed.
    :ivar estimated: Whether *rows* is an estimate made before loading.
    :ivar unit: What the load reads one at a time: ``sample``, ``stage`` or
        ``spectrum``.
    :ivar done: How many of them were read when it stopped.
    :ivar left: Those not read, each as a dict naming it.
    """

    code = "row_cap"

    def __init__(
        self,
        rows: int,
        cap: int,
        *,
        estimated: bool,
        unit: str,
        done: int,
        left: Sequence[dict[str, Any]],
        hint: str,
    ):
        self.rows = rows
        self.cap = cap
        self.estimated = estimated
        self.unit = unit
        self.done = done
        self.left = list(left)
        if estimated:
            said = f"is estimated at {rows:,} rows or more"
        else:
            said = f"passed {rows:,} rows after {done} {unit}(s)"
        super().__init__(
            f"The load {said}, over the cap of max_rows={cap:,}, and was "
            f"stopped; {len(self.left)} {unit}(s) were left unread. {hint}"
        )

    def details(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "cap": self.cap,
            "estimated": self.estimated,
            "unit": self.unit,
            "done": self.done,
            "n_left": len(self.left),
            "left": _listed(self.left),
        }


class AccessDenied(ToolkitError):
    """A 403: the token's user lacks the role an operation needs.

    :ivar operation: What the call set out to do, e.g. ``"read peaks of"``.
    :ivar target: The object it acted on, if known.
    :ivar required_role: The role the operation needs, and where.
    :ivar current_role: The user's role in the workspace, where the toolkit
        knows it.
    """

    code = "access_denied"

    def __init__(
        self,
        operation: str,
        target: Ref | None,
        required_role: str,
        current_role: str | None = None,
        workspace: str | None = None,
    ):
        self.operation = operation
        self.target = target
        self.required_role = required_role
        self.current_role = current_role
        self.workspace = workspace
        where = f" on workspace {workspace!r}" if workspace else ""
        have = (
            f"You have {current_role}{where}; "
            if current_role
            else f"You have no role{where} that allows it; "
        )
        super().__init__(
            f"Refused to {operation} {target or 'the object'}. {have}"
            f"it needs {required_role}. Ask a workspace admin for the role, "
            "or work with objects you can read."
        )

    def details(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "target": self.target.to_dict() if self.target else None,
            "required_role": self.required_role,
            "current_role": self.current_role,
            "workspace": self.workspace,
        }


class NotAuthenticated(ToolkitError):
    """A 401: the token is missing, expired, or for another service name."""

    code = "not_authenticated"


class InvalidRequest(ToolkitError):
    """A call the toolkit or the server refuses as asked: fix the arguments."""

    code = "invalid_request"


class Unavailable(ToolkitError):
    """The server could not be reached or failed, after the SDK's retries.

    Transient by nature: the same call may succeed later. The toolkit does
    not retry it again.
    """

    code = "unavailable"


@contextmanager
def sdk_errors(
    operation: str,
    target: Ref | None = None,
    *,
    required_role: str = "guest",
    current_role: Any = None,
) -> Iterator[None]:
    """Raise the SDK's exceptions inside as :class:`ToolkitError`.

    A :class:`ToolkitError` raised inside (a row cap reached through a load's
    hook, say) passes through as it is.

    :param operation: What the calls inside do, for the message: ``"read
        peaks of"``.
    :param target: The object they act on.
    :param required_role: The role the operation needs; a read needs guest.
    :param current_role: Called on a 403 for ``(role, workspace name)``, the
        user's role where the object lives, when the caller can tell.
    """
    try:
        yield
    except ToolkitError:
        raise
    except PermissionDeniedError as e:
        role, workspace = current_role() if current_role else (None, None)
        raise AccessDenied(
            operation, target, e.required_role or required_role, role, workspace
        ) from e
    except AuthenticationError as e:
        raise NotAuthenticated(
            "The server refused the token (HTTP 401): it is missing, has "
            "expired, or was generated for another service than the one this "
            "client names (an AI agent's token needs service_name='ai-agent'). "
            "Generate a new token in Mascope's settings."
        ) from e
    except NotFoundError as e:
        raise NotFound(
            target.id if target else operation,
            target.kind if target else None,
            "It may have been deleted, or never been visible to this user.",
        ) from e
    except ValidationError as e:
        raise InvalidRequest(
            f"The server refused to {operation} {target or 'it'}: {e.message}"
        ) from e
    except (ServerError, MascopeAPIError, MascopeConnectionError) as e:
        # What is left of MascopeAPIError here is a 408 or a 429 the SDK
        # retried as transient
        raise Unavailable(
            f"Could not {operation} {target or 'it'}: {e}. The SDK already "
            "retried; try again later, or ask whoever runs the server."
        ) from e
