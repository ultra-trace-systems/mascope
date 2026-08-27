import uuid

import httpx
from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi_users.exceptions import InvalidPasswordException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from mascope_backend.api.new.roles.exceptions import InvalidRoleException
from mascope_backend.runtime import runtime


class ApiException(Exception):
    def __init__(self, user_message, tech_message, status_code):
        # Pass the user message to Exception so str(self) is not empty:
        # it is what error trackers group on and what plain f"{e}" log lines
        # render. Only the user-facing message goes here - tech_message can
        # carry internals and stays off the exception's str().
        super().__init__(user_message)
        self.user_message = user_message
        self.tech_message = tech_message
        self.status_code = status_code


class CodedHTTPException(HTTPException):
    """
    HTTPException carrying a stable machine-readable code to the client.

    A client that has to react to a specific condition must branch on the code,
    not on the user-facing prose or on a status code it shares with unrelated
    failures. ``process_exception`` copies ``error_code`` into the response's
    ``detail`` object.

    Declared here rather than beside the exceptions that raise it: the auth
    package imports this module, so the dependency only runs in this direction.
    """

    error_code: str = ""


class ClientFacingDetail:
    """
    Marker: this exception's ``detail`` is written for the caller and must
    survive the 401 genericization below.

    A 401 normally answers "please sign in", which is right for a browser
    whose session lapsed and useless to an unattended agent, which has no
    session and cannot sign in. A refusal that carries remediation the caller
    can actually act on mixes this in to keep its own wording. That wording
    reaches the response body, so it must stay a fixed, internals-free string
    - never interpolated from a token, an account or a path.

    Declared here for the same reason as CodedHTTPException above: the auth
    package imports this module, so the dependency only runs one way.
    """


#: Context prefixes added by the wrapping layers (api_controller,
#: api_controller_background_task, api_route). Used to detect messages that
#: already carry an operation context so nesting does not stack prefixes.
_CONTEXT_PREFIXES = (
    "failed to ",
    "error in ",
    "warning during ",
    "partially succeeded to ",
)


def compose_user_message(context_message: str, inner_message) -> str:
    """
    Join an error context with an inner message without duplicating wording.

    Nested controllers each wrap errors with their own context ("Failed to X"),
    so the inner message may already start with one; prepending another would
    produce messages like "Failed to X. Failed to Y. <cause>". In that case the
    innermost (most specific) message wins - the outer context still reaches
    the server log via ``process_exception``.

    :param context_message: The wrapping layer's context, e.g. "Failed to Update Workspace".
    :param inner_message: The message from the caught exception.
    :return: The combined user-facing message.
    :rtype: str
    """
    inner = str(inner_message).strip() if inner_message is not None else ""
    if not inner:
        return f"{context_message}."
    lowered = inner.lower()
    if lowered.startswith(_CONTEXT_PREFIXES) or lowered.startswith(
        context_message.strip().lower()
    ):
        return inner
    return f"{context_message}. {inner}"


class NotFoundException(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class DuplicateException(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


def is_expected_client_error(e: Exception, status_code: int) -> bool:
    """
    Whether a failure is a routine client-class outcome rather than a fault.

    This is the predicate that decides the level a failure is logged at, and
    the level is what decides whether it becomes an error-monitoring event:
    ``mascope_runtime.logging`` exports every record at WARNING or above, so
    only INFO and below are free. A bad request, a lapsed session, an id that
    names nothing, a duplicate, a partial-success warning - all of these are
    normal operation and nothing an operator can act on, so they stay at
    INFO. Anything mapped to a 5xx, and the 4xx-mapped exception types that
    still signal a server-side fault (``SQLAlchemyError``, ``AttributeError``
    and the unlisted default), are faults and log at ERROR with a traceback.

    Split out of :func:`process_exception` so that a caller which handles a
    failure itself - and therefore logs it itself - can classify it on
    exactly these terms instead of growing a second, drifting taxonomy.

    :param e: The exception being reported.
    :type e: Exception
    :param status_code: The HTTP status that exception maps to.
    :type status_code: int
    :return: True when the failure is routine and belongs at INFO.
    :rtype: bool
    """
    return status_code < 500 and isinstance(
        e,
        (
            ApiException,
            HTTPException,
            InvalidPasswordException,
            RequestValidationError,
            ValueError,
        ),
    )


def process_exception(e: Exception, context_message: str) -> ApiException:
    error_message = f"{context_message}. {str(e)}."
    # Opaque reference for correlating a client-visible error with the
    # server-side log entry. The traceback and internal details are only
    # logged, never returned to the client; genericized user messages carry a
    # short "ref:" prefix of this id so users can quote it to support.
    error_id = uuid.uuid4().hex
    tech_message = {"error_id": error_id}

    # Use pattern matching to determine user message and status code
    match e:
        case SQLAlchemyTimeoutError():
            # Connection-pool starvation (QueuePool wait exceeded
            # pool_timeout) is transient server congestion, not a client
            # fault. Report 503 so callers - the file converter in
            # particular - know to retry instead of giving up on the file.
            # Must precede SQLAlchemyError: TimeoutError subclasses it.
            user_message = (
                f"{context_message}. Server is busy, please try again "
                f"(ref: {error_id[:8]})."
            )
            status_code = 503  # Service Unavailable

        case SQLAlchemyError():
            # A database failure is this server's problem, not a malformed
            # request, and callers act on that distinction: an agent treats a
            # 4xx as final and sets the file aside, while a 5xx is worth
            # retrying. Reported as 400 it made a transient database fault
            # look like a file the server would never accept.
            user_message = (
                f"{context_message}. Database operation failed (ref: {error_id[:8]})."
            )
            status_code = 500  # Internal Server Error

        case ApiException():
            user_message = e.user_message
            status_code = e.status_code
            # Keep payloads that application code attached on purpose
            # (e.g. raise_api_warning details consumed by the frontend).
            if isinstance(e.tech_message, dict):
                tech_message = jsonable_encoder(
                    {**e.tech_message, "error_id": error_id}
                )
            elif e.tech_message:
                # Non-dict payload (e.g. a plain string): keep it under a key so
                # the error_id stays present for log correlation.
                tech_message = jsonable_encoder(
                    {"detail": e.tech_message, "error_id": error_id}
                )

        case InvalidPasswordException():
            # Password policy violation from UserManager.validate_password.
            user_message = str(e.reason)
            status_code = 400  # Bad Request

        case HTTPException():
            status_code = e.status_code
            if isinstance(e, CodedHTTPException) and e.error_code:
                tech_message = {"code": e.error_code, "error_id": error_id}

            match e:
                case ClientFacingDetail() if (
                    e.status_code == status.HTTP_401_UNAUTHORIZED and e.detail
                ):
                    # The refusal wrote remediation for whoever hit it; keep it.
                    user_message = compose_user_message(context_message, e.detail)
                case _ if e.status_code == status.HTTP_401_UNAUTHORIZED:
                    user_message = f"{context_message}. Please sign in to the Mascope."
                case _ if (
                    e.status_code == status.HTTP_400_BAD_REQUEST
                    and str(e.detail) == "ErrorCode.LOGIN_BAD_CREDENTIALS"
                ):
                    user_message = (
                        "Invalid login credentials. Please check email and password."
                    )
                case InvalidRoleException():
                    user_message = (
                        "The role is invalid. Please contact the administrator."
                    )
                case _:
                    user_message = compose_user_message(context_message, e.detail)

        # Handling for httpx timeout errors
        case httpx.TimeoutException():
            user_message = (
                f"{context_message}. Connection timed out. Please try again later."
            )
            status_code = 504  # Gateway Timeout

        case httpx.ConnectError():
            user_message = f"{context_message}. Unable to connect to the service. Please check your connection and try again."
            status_code = 503  # Service Unavailable

        case httpx.RequestError():
            user_message = (
                f"{context_message}. Error making the request to external service."
            )
            status_code = 502  # Bad Gateway

        case ValueError():
            user_message = error_message.replace("\n", "; ")
            status_code = 400  # Bad Request

        case RequestValidationError():
            # str(e) and each error's "input" field carry the raw offending
            # values (which can include credentials); never log or return them.
            # Log only the field location + validator message.
            error_messages = [error["msg"] for error in e.errors()]
            safe_details = [
                f"{'.'.join(str(p) for p in error.get('loc', []))}: {error['msg']}"
                for error in e.errors()
            ]
            error_message = (
                f"{context_message}. Request validation failed: "
                f"{'; '.join(safe_details)}."
            )
            combined_error_message = "; ".join(error_messages)
            user_message = f"{context_message}. {combined_error_message}"
            status_code = 422  # Unprocessable entity

        case AttributeError():
            # str(e) can name internal attributes/objects; keep it out of the
            # client response (the full message is still logged server-side).
            # 500 like RuntimeError below: an attribute the code did not expect
            # is a fault here, and nothing the caller sent can fix it.
            user_message = f"{context_message}. Unexpected error (ref: {error_id[:8]})."
            status_code = 500  # Internal Server Error

        case RuntimeError():
            # RuntimeError messages often embed internal paths/state; do not
            # echo them to the client (logged server-side under error_id).
            user_message = f"{context_message}. Unexpected error (ref: {error_id[:8]})."
            status_code = 500  # Internal Server Error

        case _:  # Default case
            # Do not echo str(e) for unexpected exceptions: messages such as
            # FileNotFoundError include internal filesystem paths.
            user_message = f"{context_message}. Unexpected error (ref: {error_id[:8]})."
            status_code = 500  # Internal Server Error

    # Log the exception with context server-side, correlated by error_id.
    # Routine client-class failures (bad input, auth, not-found, duplicates,
    # partial-success warnings) are part of normal operation and log at INFO
    # (see mascope_runtime.logging for the level policy). Everything mapped
    # to a 5xx status - and 4xx-mapped exception types that signal
    # server-side faults (SQLAlchemyError, AttributeError) - logs at ERROR
    # with its traceback. RequestValidationError is always INFO and its
    # message redacted: its str()/traceback render the offending request
    # "input" values, which can be credentials.
    expected_client_error = is_expected_client_error(e, status_code)
    with runtime.logger.contextualize(status_code=status_code, error_id=error_id):
        if expected_client_error:
            runtime.logger.info(error_message)
        else:
            runtime.logger.exception(error_message)

    return ApiException(user_message, tech_message, status_code)


def api_e_response_json(e: ApiException):
    return JSONResponse(
        status_code=e.status_code,
        content={"error": e.user_message, "detail": e.tech_message},
    )


def handle_exception(
    e, context_message: str, response_type: str = "http"
) -> JSONResponse:
    """
    Handles exceptions by processing them and returning an appropriate response.

    :param e: The exception that was raised.
    :param context_message: A context message for better error understanding.
    :param response_type: The type of response to return, defaults to "http".
    :return: A JSONResponse for HTTP response types.
    """
    # Process the exception
    processed_exception = process_exception(e, context_message)

    # Handle based on the response type
    if response_type == "http":
        return api_e_response_json(processed_exception)
    # other response types like "sio" can be added here


def raise_api_warning(message: str, tech_message: dict, status_code: int = 200) -> None:
    """
    Raises an ApiException with a warning status code, indicating a warning during operation.

    This function creates a standardized way to raise warning level issues
    in the application. The warning will:
    1. Be logged to the server logs
    2. Be returned as a response with the specified status code
    3. Be displayed as a notification in the UI if a valid SID is available

    Status code usage:
    - 200: General warnings for both regular controllers and background tasks
    - 207: Multi-status/batch operations with partial success (some succeeded, some failed)

    Example:
    1) For general warnings (both controllers and background tasks)
        raise_api_warning(
            "Processing completed with warnings",
            {"warning_details": "..."}
        )

    2) For batch operations with partial success
        raise_api_warning(
            "Some items could not be processed",
            {"skipped_items": ["item1", "item2"]},
            status_code=207
        )

    :param message: The user-facing warning message.
    :type message: str
    :param tech_message: The technical details to include in the warning.
    :type tech_message: dict
    :param status_code: HTTP status code (200 for general warnings, 207 for multi-status operations).
    :type status_code: int
    :raises ApiException: Always raises an ApiException with the provided message and tech details.
    """
    # INFO on purpose: these are user-facing partial-success notices delivered
    # to the UI, not operator signal.
    runtime.logger.info(message)
    raise ApiException(
        user_message=message,
        tech_message=tech_message,
        status_code=status_code,
    )
